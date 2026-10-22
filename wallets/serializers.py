from decimal import Decimal
from rest_framework import serializers
from rest_framework.validators import UniqueValidator
from .models import RechargeTransaction, Wallet, WalletTransaction


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = (
            "id",
            "transaction_type",
            "amount",
            "balance_before",
            "balance_after",
            "reference",
            "description",
            "created_at",
        )


class WalletSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="user.id", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)
    recent_transactions = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = (
            "id",
            "user_id",
            "username",
            "balance",
            "updated_at",
            "recent_transactions",
        )

    def get_recent_transactions(self, obj) -> list[dict]:
        txs = obj.transactions.all()[:10]
        return WalletTransactionSerializer(txs, many=True).data


class InitiateRechargeSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    reference = serializers.CharField(
        required=False,
        max_length=100,
        allow_blank=True,
        validators=[UniqueValidator(queryset=RechargeTransaction.objects.all())],
    )


class RechargeCallbackSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=100)
    status = serializers.ChoiceField(choices=["success", "failed"])


class RechargeTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RechargeTransaction
        fields = (
            "id",
            "amount",
            "reference",
            "status",
            "created_at",
            "updated_at",
        )
