from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class SessionStatus(models.TextChoices):
    UNASSIGNED = "unassigned", "Unassigned"
    ASSIGNED = "assigned", "Assigned"
    ACCEPTED_BY_PROVIDER = "accepted_by_provider", "Accepted by Provider"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class SessionEndedBy(models.TextChoices):
    USER = "USER", "User"
    PROVIDER = "PROVIDER", "Provider"
    SYSTEM = "SYSTEM", "System"


class ConsultationSession(models.Model):
    """
    Represents a 1:1 consultation session with a defined lifecycle,
    per-minute billing, and wallet deduction.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="client_sessions",
        help_text="Client who booked the session",
    )
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="provider_sessions",
        help_text="Provider conducting the session",
    )
    status = models.CharField(
        max_length=30,
        choices=SessionStatus.choices,
        default=SessionStatus.UNASSIGNED,
        db_index=True,
    )
    per_minute_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("50.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Fixed per-minute cost for this session (e.g., ₹50.00)",
    )

    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.CharField(
        max_length=20,
        choices=SessionEndedBy.choices,
        null=True,
        blank=True,
    )
    end_reason = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Reason for session ending (e.g. 'Insufficient Balance', 'User ended session')",
    )

    billed_minutes = models.PositiveIntegerField(
        default=0,
        help_text="Total number of whole minutes charged so far",
    )
    total_billed_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    total_duration_seconds = models.PositiveIntegerField(
        default=0,
        help_text="Actual talk/session duration in seconds",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"Session #{self.id} (user={self.user.username}, "
            f"provider={self.provider.username if self.provider else 'None'}, "
            f"status={self.status})"
        )

    @property
    def is_active(self) -> bool:
        return self.status == SessionStatus.IN_PROGRESS


class SessionBillingRecord(models.Model):
    """
    Granular audit record for each billed minute of a session.
    The unique constraint ensures that the exact same minute can never be billed twice.
    """
    session = models.ForeignKey(
        ConsultationSession,
        on_delete=models.CASCADE,
        related_name="billing_records",
    )
    minute_number = models.PositiveIntegerField(
        help_text="1-indexed sequence number of the billed minute (1, 2, 3...)",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    billed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["minute_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "minute_number"],
                name="unique_session_minute_billing",
            )
        ]

    def __str__(self):
        return f"BillingRecord(Session #{self.session_id}, min={self.minute_number}, amount={self.amount})"
