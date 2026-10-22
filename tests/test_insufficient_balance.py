from decimal import Decimal
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase
from consultations.models import ConsultationSession, SessionEndedBy, SessionStatus
from consultations.services import (
    InsufficientBalanceError,
    accept_session,
    bill_session,
    create_consultation_session,
    start_session,
)
from users.models import User, UserRole
from wallets.services import credit_wallet, get_or_create_wallet


class InsufficientBalanceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="broke_client",
            password="password123",
            role=UserRole.USER,
        )
        self.provider = User.objects.create_user(
            username="provider_doc",
            password="password123",
            role=UserRole.PROVIDER,
        )
        self.wallet = get_or_create_wallet(self.user)

    def test_scenario_1_user_with_less_than_5_minutes_balance_cannot_start_session(self):
        """
        Required Test Scenario 1:
        A user with less than 5 minutes' worth of balance cannot start a session.
        Cost = 50/min. Required = 5 x 50 = 250.
        Current balance = 240.
        """
        credit_wallet(self.wallet, Decimal("240.00"))  # < 250.00

        session = create_consultation_session(
            user=self.user,
            provider=self.provider,
            per_minute_cost=Decimal("50.00"),
        )
        accept_session(session, self.provider)

        # Attempt to start session with 240 balance
        with self.assertRaises(InsufficientBalanceError) as ctx:
            start_session(session, self.user)

        self.assertIn("Insufficient wallet balance to start session", str(ctx.exception))
        self.assertIn("Minimum balance of 250.00", str(ctx.exception))

        # Status must remain accepted_by_provider (not started)
        session.refresh_from_db()
        self.assertEqual(session.status, SessionStatus.ACCEPTED_BY_PROVIDER)

        # Now credit remaining 10 to reach exactly 250.00
        credit_wallet(self.wallet, Decimal("10.00"))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("250.00"))

        # Now start should succeed!
        session = start_session(session, self.user)
        self.assertEqual(session.status, SessionStatus.IN_PROGRESS)

    def test_scenario_3_balance_runs_out_during_active_session(self):
        """
        Required Test Scenario 3:
        During an active session, the wallet cannot cover the next minute.
        Verify that:
        ● The session automatically ends.
        ● Wallet does not become negative.
        ● The correct ending reason is recorded.

        Example matching the requirement:
        Wallet starts with 250 (5 mins).
        Session starts (per_minute_cost = 50).
        User consumes 4 minutes (4 x 50 = 200).
        Remaining wallet balance = 50.
        Wait, suppose remaining balance is 40.
        Next minute (cost 50) cannot be paid.
        -> Session automatically ends
        -> Wallet remains 40 (NOT negative!)
        -> ended_by = 'SYSTEM', end_reason = 'Insufficient Balance'.
        """
        # User has exactly 290.00 (per_minute_cost = 50.00)
        # Sufficient to start (> 250).
        credit_wallet(self.wallet, Decimal("290.00"))

        session = create_consultation_session(
            user=self.user,
            provider=self.provider,
            per_minute_cost=Decimal("50.00"),
        )
        accept_session(session, self.provider)
        start_session(session, self.user)

        # Bill 5 minutes: 5 * 50 = 250 debited.
        bill_session(session, minutes_count=5)

        self.wallet.refresh_from_db()
        self.session_refreshed = ConsultationSession.objects.get(pk=session.pk)

        # Wallet balance now = 290 - 250 = 40.00
        self.assertEqual(self.wallet.balance, Decimal("40.00"))
        self.assertEqual(self.session_refreshed.billed_minutes, 5)
        self.assertEqual(self.session_refreshed.status, SessionStatus.IN_PROGRESS)

        # Now trigger billing for the 6th minute:
        # Cost is 50.00, but wallet only has 40.00!
        billing_result = bill_session(self.session_refreshed, minutes_count=1)

        # Verify billing result
        self.assertTrue(billing_result["session_ended"])
        self.assertEqual(billing_result["reason"], "Insufficient Balance")
        self.assertEqual(billing_result["billed_minutes_count"], 0)

        # Verify session state in DB
        self.session_refreshed.refresh_from_db()
        self.assertEqual(self.session_refreshed.status, SessionStatus.COMPLETED)
        self.assertEqual(self.session_refreshed.ended_by, SessionEndedBy.SYSTEM)
        self.assertEqual(self.session_refreshed.end_reason, "Insufficient Balance")
        self.assertIsNotNone(self.session_refreshed.ended_at)

        # Verify wallet balance DID NOT BECOME NEGATIVE and remained 40.00
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("40.00"))
        self.assertGreaterEqual(self.wallet.balance, Decimal("0.00"))


class InsufficientBalanceAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="api_broke_client",
            password="password123",
            role=UserRole.USER,
        )
        self.provider = User.objects.create_user(
            username="api_doc",
            password="password123",
            role=UserRole.PROVIDER,
        )
        self.wallet = get_or_create_wallet(self.user)
        self.client.force_authenticate(user=self.user)

    def test_scenario_1_api_insufficient_balance_returns_400(self):
        # Wallet has only 100 (< 5 * 50 = 250)
        credit_wallet(self.wallet, Decimal("100.00"))

        create_res = self.client.post("/api/sessions/", {
            "provider_id": self.provider.id,
            "per_minute_cost": "50.00",
        })
        session_id = create_res.data["id"]

        session = ConsultationSession.objects.get(id=session_id)
        accept_session(session, self.provider)

        # Start session via API
        start_res = self.client.post(f"/api/sessions/{session_id}/start/")
        self.assertEqual(start_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Insufficient wallet balance to start session", start_res.data["error"])

    def test_scenario_3_api_balance_runs_out_ends_session_by_system(self):
        # Wallet has exactly 250 (5 minutes)
        credit_wallet(self.wallet, Decimal("250.00"))

        create_res = self.client.post("/api/sessions/", {
            "provider_id": self.provider.id,
            "per_minute_cost": "50.00",
        })
        session_id = create_res.data["id"]
        session = ConsultationSession.objects.get(id=session_id)
        accept_session(session, self.provider)

        start_res = self.client.post(f"/api/sessions/{session_id}/start/")
        self.assertEqual(start_res.status_code, status.HTTP_200_OK)

        # Bill 5 minutes -> balance becomes 0
        bill_res1 = self.client.post(f"/api/sessions/{session_id}/bill/", {"minutes": 5})
        self.assertEqual(bill_res1.status_code, status.HTTP_200_OK)

        wal_res = self.client.get("/api/wallet/")
        self.assertEqual(Decimal(wal_res.data["balance"]), Decimal("0.00"))

        # Bill 1 more minute -> cannot pay! Session ends by system
        bill_res2 = self.client.post(f"/api/sessions/{session_id}/bill/", {"minutes": 6})
        self.assertEqual(bill_res2.status_code, status.HTTP_200_OK)
        self.assertTrue(bill_res2.data["billing_summary"]["session_ended"])
        self.assertEqual(bill_res2.data["billing_summary"]["reason"], "Insufficient Balance")
        self.assertEqual(bill_res2.data["session"]["status"], "completed")
        self.assertEqual(bill_res2.data["session"]["ended_by"], "SYSTEM")
        self.assertEqual(bill_res2.data["session"]["end_reason"], "Insufficient Balance")

        # Wallet balance remains 0.00 (not negative)
        wal_res2 = self.client.get("/api/wallet/")
        self.assertEqual(Decimal(wal_res2.data["balance"]), Decimal("0.00"))
