from django.urls import path
from .views import (
    InitiateRechargeView,
    RechargeCallbackView,
    UserRechargeListView,
    WalletDetailView,
    WalletTransactionListView,
)

urlpatterns = [
    path("", WalletDetailView.as_view(), name="wallet-detail"),
    path("transactions/", WalletTransactionListView.as_view(), name="wallet-transactions"),
    path("recharge/", InitiateRechargeView.as_view(), name="wallet-recharge-initiate"),
    path("recharge/callback/", RechargeCallbackView.as_view(), name="wallet-recharge-callback"),
    path("recharges/", UserRechargeListView.as_view(), name="wallet-recharges-list"),
]
