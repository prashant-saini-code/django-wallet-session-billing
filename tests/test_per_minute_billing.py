from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from consultations.models import ConsultationSession, SessionBillingRecord, SessionStatus
from consultations.services import (
    accept_session,
    bill_session,
    create_consultation_session,
    start_session,
)
from users.models import User, UserRole
from wallets.services import credit_wallet, get_or_create_wallet


class PerMinuteBillingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="billing_client",
            password="password123",
            role=UserRole.USER,
        )
        self.provider = User.objects.create_user(
            username="billing_provider",
            password="password123",
            role=UserRole.PROVIDER,
        )
        self.wallet = get_or_create_wallet(self.user)
        # Fund with 500
        self.wallet = credit_wallet(self.wallet, Decimal("500.00"))
        self.wallet.refresh_from_db()

        # Setup active session with per_minute_cost = 50.00
        self.session = create_consultation_session(
            user=self.user,
            provider=self.provider,
            per_minute_cost=Decimal("50.00"),
        )
        self.session = accept_session(self.session, self.provider)
        self.session = start_session(self.session, self.user)

    def test_bill_session_two_minutes(self):
        """Billing occurs for 2 minutes -> 100 debited from wallet."""
        initial_balance = self.wallet.balance  # 500.00

        result = bill_session(session=self.session, minutes_count=2)
        self.assertEqual(result["billed_minutes_count"], 2)
        self.assertFalse(result["session_ended"])

        self.session.refresh_from_db()
        self.wallet.refresh_from_db()

        self.assertEqual(self.session.billed_minutes, 2)
        self.assertEqual(self.session.total_billed_amount, Decimal("100.00"))
        self.assertEqual(self.wallet.balance, initial_balance - Decimal("100.00"))

        records = SessionBillingRecord.objects.filter(session=self.session).order_by("minute_number")
        self.assertEqual(records.count(), 2)
        self.assertEqual(records[0].minute_number, 1)
        self.assertEqual(records[0].amount, Decimal("50.00"))
        self.assertEqual(records[1].minute_number, 2)
        self.assertEqual(records[1].amount, Decimal("50.00"))

    def test_scenario_6_duplicate_billing_does_not_charge_twice(self):
        """
        Required Test Scenario 6:
        Trigger billing more than once for the same billable minute.
        Verify that the user is not charged twice.
        """
        # Start at T0
        t0 = timezone.now()
        self.session.started_at = t0
        self.session.save(update_fields=["started_at"])

        initial_balance = self.wallet.balance  # 500.00

        # Trigger billing at T0 + 65 seconds (1 full minute elapsed)
        t_plus_65s = t0 + timezone.timedelta(seconds=65)
        res1 = bill_session(session=self.session, current_time=t_plus_65s)
        self.assertEqual(res1["billed_minutes_count"], 1)

        self.wallet.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.wallet.balance, initial_balance - Decimal("50.00"))  # 450.00
        self.assertEqual(self.session.billed_minutes, 1)

        # Trigger billing AGAIN at the same elapsed time or same elapsed minute (T0 + 70 seconds)
        t_plus_70s = t0 + timezone.timedelta(seconds=70)
        res2 = bill_session(session=self.session, current_time=t_plus_70s)
        self.assertEqual(res2["billed_minutes_count"], 0)  # No newly billed minutes

        # Verify wallet was NOT charged a second time
        self.wallet.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.wallet.balance, initial_balance - Decimal("50.00"))  # Still 450.00
        self.assertEqual(self.session.billed_minutes, 1)

        # Trigger explicit duplicate call with minutes_count=0
        res3 = bill_session(session=self.session, minutes_count=0)
        self.assertEqual(res3["billed_minutes_count"], 0)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, initial_balance - Decimal("50.00"))

        # Exactly 1 SessionBillingRecord exists for minute 1
        records = SessionBillingRecord.objects.filter(session=self.session)
        self.assertEqual(records.count(), 1)


class PerMinuteBillingAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="api_billing_client",
            password="password123",
            role=UserRole.USER,
        )
        self.provider = User.objects.create_user(
            username="api_billing_provider",
            password="password123",
            role=UserRole.PROVIDER,
        )
        self.wallet = get_or_create_wallet(self.user)
        credit_wallet(self.wallet, Decimal("300.00"))

        self.session = create_consultation_session(
            user=self.user,
            provider=self.provider,
            per_minute_cost=Decimal("50.00"),
        )
        accept_session(self.session, self.provider)
        start_session(self.session, self.user)
        self.client.force_authenticate(user=self.user)

    def test_scenario_6_api_duplicate_billing_does_not_charge_twice(self):
        """API duplicate billing verification."""
        # Bill 1 minute
        res1 = self.client.post(f"/api/sessions/{self.session.id}/bill/", {"minutes": 1})
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(res1.data["billing_summary"]["billed_minutes_count"], 1)

        bal1 = self.client.get("/api/wallet/").data["balance"]
        self.assertEqual(Decimal(bal1), Decimal("250.00"))

        # Retry the same cumulative minute target; it must not charge again.
        res2 = self.client.post(f"/api/sessions/{self.session.id}/bill/", {"minutes": 1})
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data["billing_summary"]["billed_minutes_count"], 0)

        # Balance remains 250.00
        bal2 = self.client.get("/api/wallet/").data["balance"]
        self.assertEqual(Decimal(bal2), Decimal("250.00"))
