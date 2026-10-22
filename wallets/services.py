from decimal import Decimal
from typing import Optional, Tuple
from django.db import transaction
from .models import (
    RechargeStatus,
    RechargeTransaction,
    Wallet,
    WalletTransaction,
    WalletTransactionType,
)


class InsufficientWalletBalanceError(Exception):
    """Raised when attempting to debit more than available wallet balance."""
    pass


class RechargeNotFoundError(Exception):
    """Raised when a recharge transaction reference is not found."""
    pass


def get_or_create_wallet(user) -> Wallet:
    """Retrieve or create the wallet for a user."""
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def credit_wallet(
    wallet: Wallet,
    amount: Decimal,
    reference: str = "",
    description: str = "",
) -> Wallet:
    """
    Atomically credit a wallet and log to the transaction ledger.
    Must be called within an active transaction with a locked row.
    """
    if amount <= Decimal("0.00"):
        raise ValueError("Credit amount must be greater than zero.")

    # Lock wallet row for update
    locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
    balance_before = locked_wallet.balance
    locked_wallet.balance += amount
    locked_wallet.save(update_fields=["balance", "updated_at"])

    WalletTransaction.objects.create(
        wallet=locked_wallet,
        transaction_type=WalletTransactionType.CREDIT_RECHARGE,
        amount=amount,
        balance_before=balance_before,
        balance_after=locked_wallet.balance,
        reference=reference,
        description=description or f"Credited {amount}",
    )
    return locked_wallet


def debit_wallet(
    wallet: Wallet,
    amount: Decimal,
    reference: str = "",
    description: str = "",
) -> Wallet:
    """
    Atomically debit a wallet and log to the transaction ledger.
    Ensures balance never falls below zero.
    """
    if amount <= Decimal("0.00"):
        raise ValueError("Debit amount must be greater than zero.")

    # Lock wallet row for update
    locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
    if locked_wallet.balance < amount:
        raise InsufficientWalletBalanceError(
            f"Cannot debit {amount}. Current wallet balance is {locked_wallet.balance}."
        )

    balance_before = locked_wallet.balance
    locked_wallet.balance -= amount
    locked_wallet.save(update_fields=["balance", "updated_at"])

    WalletTransaction.objects.create(
        wallet=locked_wallet,
        transaction_type=WalletTransactionType.DEBIT_SESSION,
        amount=amount,
        balance_before=balance_before,
        balance_after=locked_wallet.balance,
        reference=reference,
        description=description or f"Debited {amount}",
    )
    return locked_wallet


@transaction.atomic
def initiate_recharge(user, amount: Decimal, reference: Optional[str] = None) -> RechargeTransaction:
    """
    Initiate a mock recharge transaction in 'pending' status.
    """
    import uuid

    if amount <= Decimal("0.00"):
        raise ValueError("Recharge amount must be greater than zero.")

    ref = reference or f"REC-{uuid.uuid4().hex[:12].upper()}"
    recharge = RechargeTransaction.objects.create(
        user=user,
        amount=amount,
        reference=ref,
        status=RechargeStatus.PENDING,
    )
    return recharge


@transaction.atomic
def process_recharge_callback(
    reference: str,
    status: str,
) -> Tuple[RechargeTransaction, bool]:
    """
    Process callback/webhook for mock recharge.
    IDEMPOTENCY GUARANTEE:
    - Multiple duplicate successful callbacks will ONLY credit the wallet once.
    - Uses SELECT FOR UPDATE on the RechargeTransaction row.

    Returns:
        (recharge_instance, newly_credited: bool)
    """
    try:
        recharge = RechargeTransaction.objects.select_for_update().get(reference=reference)
    except RechargeTransaction.DoesNotExist:
        raise RechargeNotFoundError(f"Recharge transaction with reference '{reference}' not found.")

    status_str = status.lower().strip()
    if status_str not in (RechargeStatus.SUCCESS, RechargeStatus.FAILED):
        raise ValueError(f"Invalid callback status: '{status}'. Must be 'success' or 'failed'.")

    # If already marked SUCCESS, do not credit again!
    if recharge.status == RechargeStatus.SUCCESS:
        return recharge, False

    if status_str == RechargeStatus.SUCCESS:
        # Credit user's wallet atomically
        wallet = get_or_create_wallet(recharge.user)
        credit_wallet(
            wallet=wallet,
            amount=recharge.amount,
            reference=recharge.reference,
            description=f"Recharge callback success: {recharge.reference}",
        )
        recharge.status = RechargeStatus.SUCCESS
        recharge.save(update_fields=["status", "updated_at"])
        return recharge, True
    else:
        recharge.status = RechargeStatus.FAILED
        recharge.save(update_fields=["status", "updated_at"])
        return recharge, False
