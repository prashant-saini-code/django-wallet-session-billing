from django.contrib import admin
from .models import RechargeTransaction, Wallet, WalletTransaction


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance", "updated_at")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(RechargeTransaction)
class RechargeTransactionAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "amount", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("reference", "user__username")
    readonly_fields = ("created_at", "updated_at")


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ("wallet", "transaction_type", "amount", "balance_before", "balance_after", "created_at")
    list_filter = ("transaction_type", "created_at")
    search_fields = ("wallet__user__username", "reference")
    readonly_fields = ("created_at",)
