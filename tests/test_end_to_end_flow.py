from decimal import Decimal
from rest_framework import status
from rest_framework.test import APITestCase
from consultations.models import ConsultationSession
from users.models import User, UserRole


class EndToEndHappyPathTests(APITestCase):
    def setUp(self):
        # 1. Create client user and provider user
        self.client_user = User.objects.create_user(
            username="john_doe",
            password="password123",
            role=UserRole.USER,
        )
        self.provider_user = User.objects.create_user(
            username="dr_consultant",
            password="password123",
            role=UserRole.PROVIDER,
        )

    def test_scenario_2_complete_happy_path_flow(self):
        """
        Required Test Scenario 2:
        Recharge wallet
        → Create session
        → Provider accepts
        → Start session
        → Billing occurs for 2 minutes
        → End session
        Verify that the wallet is debited correctly.
        """
        # Step 1: User logs in and authenticates
        login_res = self.client.post("/api/auth/login/", {
            "username": "john_doe",
            "password": "password123",
        })
        self.assertEqual(login_res.status_code, status.HTTP_200_OK)
        user_token = login_res.data["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {user_token}")

        # Step 2: Recharge wallet with 500.00
        recharge_init = self.client.post("/api/wallet/recharge/", {"amount": "500.00"})
        self.assertEqual(recharge_init.status_code, status.HTTP_201_CREATED)
        ref = recharge_init.data["reference"]

        # Mock webhook callback completes recharge
        cb_res = self.client.post("/api/wallet/recharge/callback/", {
            "reference": ref,
            "status": "success",
        })
        self.assertEqual(cb_res.status_code, status.HTTP_200_OK)

        # Verify wallet balance is 500.00
        wallet_res = self.client.get("/api/wallet/")
        self.assertEqual(Decimal(wallet_res.data["balance"]), Decimal("500.00"))

        # Step 3: Create session with provider (per_minute_cost = 50.00)
        create_res = self.client.post("/api/sessions/", {
            "provider_id": self.provider_user.id,
            "per_minute_cost": "50.00",
        })
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)
        session_id = create_res.data["id"]
        self.assertEqual(create_res.data["status"], "assigned")

        # Step 4: Provider logs in and accepts the session
        prov_login = self.client.post("/api/auth/login/", {
            "username": "dr_consultant",
            "password": "password123",
        })
        prov_token = prov_login.data["token"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {prov_token}")

        accept_res = self.client.post(f"/api/sessions/{session_id}/accept/")
        self.assertEqual(accept_res.status_code, status.HTTP_200_OK)
        self.assertEqual(accept_res.data["status"], "accepted_by_provider")

        # Step 5: User starts session
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {user_token}")
        start_res = self.client.post(f"/api/sessions/{session_id}/start/")
        self.assertEqual(start_res.status_code, status.HTTP_200_OK)
        self.assertEqual(start_res.data["status"], "in_progress")
        self.assertIsNotNone(start_res.data["started_at"])

        # Step 6: Billing occurs for 2 minutes
        bill_res = self.client.post(f"/api/sessions/{session_id}/bill/", {"minutes": 2})
        self.assertEqual(bill_res.status_code, status.HTTP_200_OK)
        self.assertEqual(bill_res.data["billing_summary"]["billed_minutes_count"], 2)
        self.assertEqual(
            Decimal(bill_res.data["session"]["total_billed_amount"]),
            Decimal("100.00"),
        )

        # Step 7: End session
        end_res = self.client.post(f"/api/sessions/{session_id}/end/")
        self.assertEqual(end_res.status_code, status.HTTP_200_OK)
        self.assertEqual(end_res.data["status"], "completed")
        self.assertEqual(end_res.data["ended_by"], "USER")

        # Step 8: Verify that the wallet is debited correctly:
        # Started at 500.00 - (2 mins * 50.00) = 400.00
        final_wallet_res = self.client.get("/api/wallet/")
        self.assertEqual(Decimal(final_wallet_res.data["balance"]), Decimal("400.00"))

        # Check session details
        detail_res = self.client.get(f"/api/sessions/{session_id}/")
        self.assertEqual(detail_res.status_code, status.HTTP_200_OK)
        self.assertEqual(detail_res.data["billed_minutes"], 2)
        self.assertEqual(Decimal(detail_res.data["total_billed_amount"]), Decimal("100.00"))
        self.assertEqual(len(detail_res.data["billing_records"]), 2)

    def test_provider_can_end_session(self):
        """Verify provider can also end an in_progress session."""
        from consultations.services import (
            accept_session,
            create_consultation_session,
            start_session,
        )
        from wallets.services import credit_wallet, get_or_create_wallet

        wallet = get_or_create_wallet(self.client_user)
        credit_wallet(wallet, Decimal("300.00"))

        session = create_consultation_session(
            user=self.client_user,
            provider=self.provider_user,
            per_minute_cost=Decimal("50.00"),
        )
        accept_session(session, self.provider_user)
        start_session(session, self.client_user)

        # Provider ends session
        prov_login = self.client.post("/api/auth/login/", {
            "username": "dr_consultant",
            "password": "password123",
        })
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {prov_login.data['token']}")

        end_res = self.client.post(f"/api/sessions/{session.id}/end/")
        self.assertEqual(end_res.status_code, status.HTTP_200_OK)
        self.assertEqual(end_res.data["status"], "completed")
        self.assertEqual(end_res.data["ended_by"], "PROVIDER")
        self.assertEqual(end_res.data["end_reason"], "Provider ended session")
