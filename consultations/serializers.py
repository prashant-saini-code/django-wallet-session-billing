from decimal import Decimal
from rest_framework import serializers
from users.serializers import UserDetailSerializer
from .models import ConsultationSession, SessionBillingRecord


class SessionBillingRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionBillingRecord
        fields = ("id", "minute_number", "amount", "billed_at")


class ConsultationSessionSerializer(serializers.ModelSerializer):
    user = UserDetailSerializer(read_only=True)
    provider = UserDetailSerializer(read_only=True)
    billing_records = SessionBillingRecordSerializer(many=True, read_only=True)

    class Meta:
        model = ConsultationSession
        fields = (
            "id",
            "user",
            "provider",
            "status",
            "per_minute_cost",
            "started_at",
            "ended_at",
            "ended_by",
            "end_reason",
            "billed_minutes",
            "total_billed_amount",
            "total_duration_seconds",
            "billing_records",
            "created_at",
            "updated_at",
        )


class CreateSessionSerializer(serializers.Serializer):
    provider_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    per_minute_cost = serializers.DecimalField(
        required=False,
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )


class AssignProviderSerializer(serializers.Serializer):
    provider_id = serializers.IntegerField(required=True, min_value=1)


class BillSessionSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(
        required=False,
        min_value=0,
        help_text="Cumulative elapsed whole-minute target. Repeating the same value is safe.",
    )
