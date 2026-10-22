from decimal import Decimal
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase
from users.models import User, UserRole
from wallets.models import RechargeStatus, RechargeTransaction, WalletTransaction
from wallets.services import (
    get_or_create_wallet,
    initiate_recharge,
    process_recharge_callback,
)


class RechargeIdempotencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="recharge_user",
            password="password123",
            role=UserRole.USER,
        )
        self.wallet = get_or_create_wallet(self.user)

    def test_mock_recharge_success_credits_wallet(self):
        """Standard mock recharge flow: initiate -> success callback -> wallet credited."""
        recharge = initiate_recharge(user=self.user, amount=Decimal("100.00"), reference="TEST-REF-100")
        self.assertEqual(recharge.status, RechargeStatus.PENDING)
        self.assertEqual(self.wallet.balance, Decimal("0.00"))

        recharge, newly_credited = process_recharge_callback(
            reference="TEST-REF-100",
            status="success",
        )
        self.assertTrue(newly_credited)
        self.assertEqual(recharge.status, RechargeStatus.SUCCESS)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))

    def test_scenario_5_duplicate_recharge_callback_credits_only_once(self):
        """
        Required Test Scenario 5:
        Process the same successful recharge callback twice.
        Verify that the wallet is credited only once.
        """
        ref = "IDEMPOTENT-REF-500"
        initiate_recharge(user=self.user, amount=Decimal("500.00"), reference=ref)

        # First callback: should succeed and credit 500.00
        recharge1, newly_credited1 = process_recharge_callback(reference=ref, status="success")
        self.assertTrue(newly_credited1)
        self.assertEqual(recharge1.status, RechargeStatus.SUCCESS)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500.00"))

        # Second callback: same reference, status=success
        recharge2, newly_credited2 = process_recharge_callback(reference=ref, status="success")
        self.assertFalse(newly_credited2)
        self.assertEqual(recharge2.status, RechargeStatus.SUCCESS)

        # Crucial check: wallet balance is still exactly 500.00 (NOT 1000.00)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500.00"))

        # Third callback (e.g. delayed network retry): still credited only once
        recharge3, newly_credited3 = process_recharge_callback(reference=ref, status="success")
        self.assertFalse(newly_credited3)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500.00"))

        # Check ledger entries: exactly ONE credit entry exists
        ledger_count = WalletTransaction.objects.filter(
            wallet=self.wallet,
            reference=ref,
        ).count()
        self.assertEqual(ledger_count, 1)

    def test_failed_recharge_does_not_credit_wallet(self):
        ref = "FAIL-REF-001"
        initiate_recharge(user=self.user, amount=Decimal("150.00"), reference=ref)

        recharge, newly_credited = process_recharge_callback(reference=ref, status="failed")
        self.assertFalse(newly_credited)
        self.assertEqual(recharge.status, RechargeStatus.FAILED)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("0.00"))


class RechargeAPIEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="api_user",
            password="password123",
            role=UserRole.USER,
        )
        self.wallet = get_or_create_wallet(self.user)
        self.client.force_authenticate(user=self.user)

    def test_initiate_and_complete_recharge_via_api(self):
        # 1. Initiate recharge
        init_res = self.client.post("/api/wallet/recharge/", {"amount": "250.00"})
        self.assertEqual(init_res.status_code, status.HTTP_201_CREATED)
        reference = init_res.data["reference"]
        self.assertEqual(init_res.data["status"], "pending")

        # 2. First callback -> HTTP 200, newly_credited = True
        cb_res_1 = self.client.post(
            "/api/wallet/recharge/callback/",
            {"reference": reference, "status": "success"},
        )
        self.assertEqual(cb_res_1.status_code, status.HTTP_200_OK)
        self.assertTrue(cb_res_1.data["newly_credited"])

        # Check balance
        bal_res = self.client.get("/api/wallet/")
        self.assertEqual(Decimal(bal_res.data["balance"]), Decimal("250.00"))

        # 3. Duplicate callback -> HTTP 200, newly_credited = False
        cb_res_2 = self.client.post(
            "/api/wallet/recharge/callback/",
            {"reference": reference, "status": "success"},
        )
        self.assertEqual(cb_res_2.status_code, status.HTTP_200_OK)
        self.assertFalse(cb_res_2.data["newly_credited"])

        # Check balance: still 250.00
        bal_res2 = self.client.get("/api/wallet/")
        self.assertEqual(Decimal(bal_res2.data["balance"]), Decimal("250.00"))

    def test_callback_with_unknown_reference_returns_404(self):
        res = self.client.post(
            "/api/wallet/recharge/callback/",
            {"reference": "NON-EXISTENT-REF", "status": "success"},
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
