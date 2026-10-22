from decimal import Decimal
from typing import Any, Dict, Optional
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from wallets.models import Wallet
from wallets.services import debit_wallet, get_or_create_wallet
from .models import (
    ConsultationSession,
    SessionBillingRecord,
    SessionEndedBy,
    SessionStatus,
)


class InvalidStateTransitionError(Exception):
    """Raised when an illegal lifecycle transition is attempted."""
    pass


class InsufficientBalanceError(Exception):
    """Raised when user wallet balance is below the required threshold."""
    pass


class ActiveSessionExistsError(Exception):
    """Raised when a user already has an active session in progress."""
    pass


class SessionPermissionError(Exception):
    """Raised when a user/provider attempts an action on a session they do not own."""
    pass


def create_consultation_session(
    user,
    provider=None,
    per_minute_cost: Optional[Decimal] = None,
) -> ConsultationSession:
    """
    Create a new consultation session for a client user.
    """
    if getattr(user, "role", None) != "USER":
        raise SessionPermissionError("Only users with role 'USER' can book consultation sessions.")

    if provider is not None:
        if getattr(provider, "role", None) != "PROVIDER":
            raise SessionPermissionError("Assigned provider must have role 'PROVIDER'.")
        initial_status = SessionStatus.ASSIGNED
    else:
        initial_status = SessionStatus.UNASSIGNED

    cost = per_minute_cost or getattr(settings, "DEFAULT_PER_MINUTE_COST", Decimal("50.00"))
    if cost <= Decimal("0.00"):
        raise ValueError("Per-minute cost must be greater than zero.")

    session = ConsultationSession.objects.create(
        user=user,
        provider=provider,
        status=initial_status,
        per_minute_cost=cost,
    )
    return session


@transaction.atomic
def assign_provider(
    session: ConsultationSession,
    provider,
    actor,
) -> ConsultationSession:
    """
    Assign a provider to an unassigned consultation session.
    """
    session = ConsultationSession.objects.select_for_update().get(pk=session.pk)

    if session.user_id != actor.id and not actor.is_staff:
        raise SessionPermissionError("Only the session owner or admin can assign a provider.")

    if session.status != SessionStatus.UNASSIGNED:
        raise InvalidStateTransitionError(
            f"Cannot assign provider to a session in '{session.status}' state. "
            f"Only '{SessionStatus.UNASSIGNED}' sessions can be assigned."
        )

    if getattr(provider, "role", None) != "PROVIDER":
        raise SessionPermissionError("Assigned provider must have role 'PROVIDER'.")

    session.provider = provider
    session.status = SessionStatus.ASSIGNED
    session.save(update_fields=["provider", "status", "updated_at"])
    return session


@transaction.atomic
def accept_session(
    session: ConsultationSession,
    provider,
) -> ConsultationSession:
    """
    Provider accepts an assigned consultation session.
    """
    session = ConsultationSession.objects.select_for_update().get(pk=session.pk)

    if session.status != SessionStatus.ASSIGNED:
        raise InvalidStateTransitionError(
            f"Cannot accept session in '{session.status}' state. "
            f"Session must be in '{SessionStatus.ASSIGNED}' state."
        )

    if session.provider_id != provider.id and not provider.is_staff:
        raise SessionPermissionError("Only the assigned provider can accept this session.")

    session.status = SessionStatus.ACCEPTED_BY_PROVIDER
    session.save(update_fields=["status", "updated_at"])
    return session


@transaction.atomic
def start_session(
    session: ConsultationSession,
    actor,
    start_time: Optional[timezone.datetime] = None,
) -> ConsultationSession:
    """
    User starts an accepted consultation session.
    Enforces:
    1. Session must be in 'accepted_by_provider' state.
    2. User cannot have more than one active ('in_progress') session.
    3. Minimum balance must be at least 5 x per_minute_cost.
    4. Fixes per_minute_cost and started_at.
    """
    session = ConsultationSession.objects.select_for_update().get(pk=session.pk)

    if session.user_id != actor.id and not actor.is_staff:
        raise SessionPermissionError("Only the session client can start the session.")

    if session.status != SessionStatus.ACCEPTED_BY_PROVIDER:
        raise InvalidStateTransitionError(
            f"Cannot start session in '{session.status}' state. "
            f"Session must be '{SessionStatus.ACCEPTED_BY_PROVIDER}' before starting."
        )

    # Check for another active session for this user
    active_session_exists = ConsultationSession.objects.filter(
        user=session.user,
        status=SessionStatus.IN_PROGRESS,
    ).exclude(pk=session.pk).exists()

    if active_session_exists:
        raise ActiveSessionExistsError(
            "User already has an active session in progress. Complete or end the active session first."
        )

    # Check minimum balance: 5 x per_minute_cost
    wallet = Wallet.objects.select_for_update().get(user=session.user)
    required_multiplier = getattr(settings, "MINIMUM_START_BALANCE_MULTIPLIER", 5)
    min_required_balance = session.per_minute_cost * required_multiplier

    if wallet.balance < min_required_balance:
        raise InsufficientBalanceError(
            f"Insufficient wallet balance to start session. "
            f"Minimum balance of {min_required_balance} (5 × per-minute cost of {session.per_minute_cost}) "
            f"is required. Current balance: {wallet.balance}."
        )

    session.status = SessionStatus.IN_PROGRESS
    session.started_at = start_time or timezone.now()
    session.save(update_fields=["status", "started_at", "updated_at"])
    return session


@transaction.atomic
def bill_session(
    session: ConsultationSession,
    current_time: Optional[timezone.datetime] = None,
    minutes_count: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Synchronous per-minute billing mechanism for an active session.
    Deducts per-minute cost for each unbilled minute from user wallet.
    Idempotent: prevents duplicate billing for already charged minutes.

    If wallet cannot cover the next minute:
    - Wallet is NOT debited for that minute (never becomes negative).
    - Session automatically ends with status 'completed',
      ended_by='SYSTEM', end_reason='Insufficient Balance'.
    - Persists actual talk/billable time.

    Returns dict with details:
        {
            "billed_minutes_count": int,
            "session_ended": bool,
            "reason": str or None,
            "total_billed_amount": Decimal,
            "session_status": str,
        }
    """
    session = ConsultationSession.objects.select_for_update().get(pk=session.pk)

    if session.status != SessionStatus.IN_PROGRESS:
        raise InvalidStateTransitionError(
            f"Cannot bill session in '{session.status}' state. "
            f"Session must be '{SessionStatus.IN_PROGRESS}'."
        )

    now = current_time or timezone.now()

    # Determine target billable minutes
    if minutes_count is not None:
        if minutes_count < 0:
            raise ValueError("minutes_count cannot be negative.")
        target_minutes = session.billed_minutes + minutes_count
    else:
        if not session.started_at:
            raise ValueError("Session does not have started_at set.")
        elapsed_seconds = max(0, int((now - session.started_at).total_seconds()))
        target_minutes = elapsed_seconds // 60

    if target_minutes <= session.billed_minutes:
        return {
            "billed_minutes_count": 0,
            "session_ended": False,
            "reason": None,
            "total_billed_amount": session.total_billed_amount,
            "session_status": session.status,
        }

    wallet = Wallet.objects.select_for_update().get(user=session.user)
    charged_count = 0

    for minute_num in range(session.billed_minutes + 1, target_minutes + 1):
        # Check if wallet can pay for this minute
        if wallet.balance < session.per_minute_cost:
            # Cannot pay for the next minute!
            # End session automatically by system
            end_session_by_system(
                session=session,
                end_reason="Insufficient Balance",
                ended_at=now,
            )
            return {
                "billed_minutes_count": charged_count,
                "session_ended": True,
                "reason": "Insufficient Balance",
                "total_billed_amount": session.total_billed_amount,
                "session_status": session.status,
            }

        # Wallet has sufficient balance: debit and record
        debit_wallet(
            wallet=wallet,
            amount=session.per_minute_cost,
            reference=f"session_{session.id}_min_{minute_num}",
            description=f"Session #{session.id} minute {minute_num} billing",
        )

        SessionBillingRecord.objects.create(
            session=session,
            minute_number=minute_num,
            amount=session.per_minute_cost,
        )

        session.billed_minutes += 1
        session.total_billed_amount += session.per_minute_cost
        charged_count += 1

    session.save(update_fields=["billed_minutes", "total_billed_amount", "updated_at"])

    return {
        "billed_minutes_count": charged_count,
        "session_ended": False,
        "reason": None,
        "total_billed_amount": session.total_billed_amount,
        "session_status": session.status,
    }


@transaction.atomic
def end_session_by_user(
    session: ConsultationSession,
    actor,
    end_time: Optional[timezone.datetime] = None,
) -> ConsultationSession:
    """
    User manually ends an in_progress session.
    Bills any remaining elapsed unbilled time and completes session.
    """
    session = ConsultationSession.objects.select_for_update().get(pk=session.pk)

    if session.user_id != actor.id and not actor.is_staff:
        raise SessionPermissionError("Only the session client can end the session via user action.")

    if session.status != SessionStatus.IN_PROGRESS:
        raise InvalidStateTransitionError(
            f"Cannot end session in '{session.status}' state. "
            f"Session must be '{SessionStatus.IN_PROGRESS}' to end."
        )

    now = end_time or timezone.now()

    # Finalize billing for any elapsed unbilled minutes
    billing_result = bill_session(session, current_time=now)
    session.refresh_from_db()

    # If already ended by system during billing, return
    if session.status == SessionStatus.COMPLETED:
        return session

    session.status = SessionStatus.COMPLETED
    session.ended_at = now
    session.ended_by = SessionEndedBy.USER
    session.end_reason = "User ended session"

    if session.started_at:
        session.total_duration_seconds = max(0, int((now - session.started_at).total_seconds()))

    session.save(update_fields=[
        "status",
        "ended_at",
        "ended_by",
        "end_reason",
        "total_duration_seconds",
        "updated_at",
    ])
    return session


@transaction.atomic
def end_session_by_provider(
    session: ConsultationSession,
    actor,
    end_time: Optional[timezone.datetime] = None,
) -> ConsultationSession:
    """
    Provider manually ends an in_progress session.
    Bills any remaining elapsed unbilled time and completes session.
    """
    session = ConsultationSession.objects.select_for_update().get(pk=session.pk)

    if session.provider_id != actor.id and not actor.is_staff:
        raise SessionPermissionError("Only the assigned provider can end the session via provider action.")

    if session.status != SessionStatus.IN_PROGRESS:
        raise InvalidStateTransitionError(
            f"Cannot end session in '{session.status}' state. "
            f"Session must be '{SessionStatus.IN_PROGRESS}' to end."
        )

    now = end_time or timezone.now()

    # Finalize billing for any elapsed unbilled minutes
    bill_session(session, current_time=now)
    session.refresh_from_db()

    if session.status == SessionStatus.COMPLETED:
        return session

    session.status = SessionStatus.COMPLETED
    session.ended_at = now
    session.ended_by = SessionEndedBy.PROVIDER
    session.end_reason = "Provider ended session"

    if session.started_at:
        session.total_duration_seconds = max(0, int((now - session.started_at).total_seconds()))

    session.save(update_fields=[
        "status",
        "ended_at",
        "ended_by",
        "end_reason",
        "total_duration_seconds",
        "updated_at",
    ])
    return session


@transaction.atomic
def end_session_by_system(
    session: ConsultationSession,
    end_reason: str = "Insufficient Balance",
    ended_at: Optional[timezone.datetime] = None,
) -> ConsultationSession:
    """
    System automatically terminates an active session (e.g., when balance runs out).
    """
    session = ConsultationSession.objects.select_for_update().get(pk=session.pk)

    now = ended_at or timezone.now()
    session.status = SessionStatus.COMPLETED
    session.ended_at = now
    session.ended_by = SessionEndedBy.SYSTEM
    session.end_reason = end_reason

    if session.started_at:
        session.total_duration_seconds = max(0, int((now - session.started_at).total_seconds()))

    session.save(update_fields=[
        "status",
        "ended_at",
        "ended_by",
        "end_reason",
        "total_duration_seconds",
        "updated_at",
    ])
    return session


@transaction.atomic
def cancel_session(
    session: ConsultationSession,
    actor,
    reason: str = "Cancelled by user",
) -> ConsultationSession:
    """
    Cancel a session before it has started (from unassigned, assigned, or accepted).
    """
    session = ConsultationSession.objects.select_for_update().get(pk=session.pk)

    if session.user_id != actor.id and session.provider_id != actor.id and not actor.is_staff:
        raise SessionPermissionError("Only the session participants or admin can cancel the session.")

    cancellable_statuses = [
        SessionStatus.UNASSIGNED,
        SessionStatus.ASSIGNED,
        SessionStatus.ACCEPTED_BY_PROVIDER,
    ]
    if session.status not in cancellable_statuses:
        raise InvalidStateTransitionError(
            f"Cannot cancel session in '{session.status}' state. "
            f"Only unstarted sessions can be cancelled."
        )

    session.status = SessionStatus.CANCELLED
    session.end_reason = reason
    session.ended_at = timezone.now()
    session.save(update_fields=["status", "end_reason", "ended_at", "updated_at"])
    return session
