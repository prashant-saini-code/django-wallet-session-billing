from decimal import Decimal
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase
from users.models import User, UserRole
from wallets.models import Wallet, WalletTransaction, WalletTransactionType
from wallets.services import (
    InsufficientWalletBalanceError,
    credit_wallet,
    debit_wallet,
    get_or_create_wallet,
)


class WalletModelAndServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="alice@example.com",
            password="password123",
            role=UserRole.USER,
        )
        self.wallet = get_or_create_wallet(self.user)

    def test_wallet_created_with_zero_balance(self):
        self.assertEqual(self.wallet.balance, Decimal("0.00"))

    def test_credit_wallet_increases_balance_and_creates_ledger(self):
        with transaction.atomic():
            credit_wallet(
                wallet=self.wallet,
                amount=Decimal("150.00"),
                reference="REF-001",
                description="Test Credit",
            )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("150.00"))

        ledger_entry = WalletTransaction.objects.filter(wallet=self.wallet).first()
        self.assertIsNotNone(ledger_entry)
        self.assertEqual(ledger_entry.amount, Decimal("150.00"))
        self.assertEqual(ledger_entry.balance_before, Decimal("0.00"))
        self.assertEqual(ledger_entry.balance_after, Decimal("150.00"))
        self.assertEqual(ledger_entry.transaction_type, WalletTransactionType.CREDIT_RECHARGE)

    def test_debit_wallet_decreases_balance_and_creates_ledger(self):
        with transaction.atomic():
            credit_wallet(self.wallet, Decimal("200.00"))
            debit_wallet(self.wallet, Decimal("50.00"), reference="SESSION-1")

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("150.00"))

        debit_entry = WalletTransaction.objects.filter(
            wallet=self.wallet,
            transaction_type=WalletTransactionType.DEBIT_SESSION,
        ).first()
        self.assertIsNotNone(debit_entry)
        self.assertEqual(debit_entry.amount, Decimal("50.00"))
        self.assertEqual(debit_entry.balance_before, Decimal("200.00"))
        self.assertEqual(debit_entry.balance_after, Decimal("150.00"))

    def test_debit_wallet_insufficient_balance_raises_error(self):
        with transaction.atomic():
            credit_wallet(self.wallet, Decimal("40.00"))

        with self.assertRaises(InsufficientWalletBalanceError):
            with transaction.atomic():
                debit_wallet(self.wallet, Decimal("50.00"))

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("40.00"))

    def test_wallet_balance_cannot_be_negative_database_constraint(self):
        """Verify database-level check constraint prevents negative balance."""
        self.wallet.balance = Decimal("-10.00")
        with self.assertRaises(IntegrityError):
            self.wallet.save()


class WalletAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="bob",
            password="password123",
            role=UserRole.USER,
        )
        self.wallet = get_or_create_wallet(self.user)
        self.client.force_authenticate(user=self.user)

    def test_get_wallet_detail(self):
        response = self.client.get("/api/wallet/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(response.data["balance"]), Decimal("0.00"))
        self.assertEqual(response.data["username"], "bob")

    def test_recharge_rejects_invalid_amount_and_duplicate_reference(self):
        invalid_amount = self.client.post("/api/wallet/recharge/", {"amount": "0.00"})
        self.assertEqual(invalid_amount.status_code, status.HTTP_400_BAD_REQUEST)

        first = self.client.post(
            "/api/wallet/recharge/",
            {"amount": "25.00", "reference": "UNIQUE-REF"},
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        duplicate = self.client.post(
            "/api/wallet/recharge/",
            {"amount": "25.00", "reference": "UNIQUE-REF"},
        )
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("reference", duplicate.data)

    def test_unauthenticated_wallet_access_denied(self):
        self.client.logout()
        response = self.client.get("/api/wallet/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
