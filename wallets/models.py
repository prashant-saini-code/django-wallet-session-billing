import uuid
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import CheckConstraint, Q


class Wallet(models.Model):
    """
    User wallet maintaining prepaid balance.
    Enforces non-negative balance constraint at both DB and application levels.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet",
    )
    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            CheckConstraint(
                condition=Q(balance__gte=Decimal("0.00")),
                name="wallet_balance_non_negative",
            )
        ]

    def __str__(self):
        return f"Wallet(user={self.user.username}, balance={self.balance})"


class RechargeStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class RechargeTransaction(models.Model):
    """
    Tracks mock recharge attempts and their lifecycle.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recharges",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    reference = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Unique external transaction reference ID",
    )
    status = models.CharField(
        max_length=20,
        choices=RechargeStatus.choices,
        default=RechargeStatus.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RechargeTx(ref={self.reference}, user={self.user.username}, amount={self.amount}, status={self.status})"


class WalletTransactionType(models.TextChoices):
    CREDIT_RECHARGE = "recharge", "Wallet Recharge"
    DEBIT_SESSION = "session_billing", "Session Billing"


class WalletTransaction(models.Model):
    """
    Immutable ledger of all wallet credits and debits for full auditability.
    """
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    transaction_type = models.CharField(
        max_length=30,
        choices=WalletTransactionType.choices,
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    balance_before = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=100, blank=True, default="")
    description = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"Ledger({self.wallet.user.username}, {self.transaction_type}, "
            f"amount={self.amount}, balance_after={self.balance_after})"
        )
