from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers
from drf_spectacular.utils import extend_schema, inline_serializer
from .models import RechargeTransaction, Wallet, WalletTransaction
from .serializers import (
    InitiateRechargeSerializer,
    RechargeCallbackSerializer,
    RechargeTransactionSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from .services import (
    RechargeNotFoundError,
    get_or_create_wallet,
    initiate_recharge,
    process_recharge_callback,
)


class WalletDetailView(APIView):
    """View current user's wallet balance and recent activity."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=WalletSerializer)
    def get(self, request):
        wallet = get_or_create_wallet(request.user)
        serializer = WalletSerializer(wallet)
        return Response(serializer.data, status=status.HTTP_200_OK)


class WalletTransactionListView(generics.ListAPIView):
    """List all transactions from the user's wallet ledger."""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = WalletTransactionSerializer

    def get_queryset(self):
        wallet = get_or_create_wallet(self.request.user)
        return WalletTransaction.objects.filter(wallet=wallet).order_by("-created_at")


class InitiateRechargeView(APIView):
    """Initiate a mock recharge for the authenticated user."""
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=InitiateRechargeSerializer, responses={201: RechargeTransactionSerializer})
    def post(self, request):
        serializer = InitiateRechargeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        amount = serializer.validated_data["amount"]
        reference = serializer.validated_data.get("reference") or None

        recharge = initiate_recharge(
            user=request.user,
            amount=amount,
            reference=reference,
        )
        return Response(
            RechargeTransactionSerializer(recharge).data,
            status=status.HTTP_201_CREATED,
        )


class RechargeCallbackView(APIView):
    """
    Mock webhook/callback endpoint to mark recharge as success or failed.
    Idempotent: Duplicate calls for an already successful transaction do not double-credit.
    """
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        request=RechargeCallbackSerializer,
        responses=inline_serializer(
            name="RechargeCallbackResponse",
            fields={
                "message": serializers.CharField(),
                "transaction": RechargeTransactionSerializer(),
                "newly_credited": serializers.BooleanField(),
            },
        ),
    )
    def post(self, request):
        serializer = RechargeCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reference = serializer.validated_data["reference"]
        cb_status = serializer.validated_data["status"]

        try:
            recharge, newly_credited = process_recharge_callback(
                reference=reference,
                status=cb_status,
            )
        except RechargeNotFoundError as e:
            return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "message": (
                    "Recharge processed and wallet credited."
                    if newly_credited
                    else "Callback received. Transaction already processed."
                ),
                "transaction": RechargeTransactionSerializer(recharge).data,
                "newly_credited": newly_credited,
            },
            status=status.HTTP_200_OK,
        )


class UserRechargeListView(generics.ListAPIView):
    """List all recharge transactions for the authenticated user."""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = RechargeTransactionSerializer

    def get_queryset(self):
        return RechargeTransaction.objects.filter(user=self.request.user).order_by("-created_at")
