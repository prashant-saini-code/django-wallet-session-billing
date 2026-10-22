from django.contrib import admin
from .models import ConsultationSession, SessionBillingRecord


class SessionBillingRecordInline(admin.TabularInline):
    model = SessionBillingRecord
    extra = 0
    readonly_fields = ("minute_number", "amount", "billed_at")
    can_delete = False


@admin.register(ConsultationSession)
class ConsultationSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "provider",
        "status",
        "per_minute_cost",
        "billed_minutes",
        "total_billed_amount",
        "ended_by",
        "started_at",
        "ended_at",
    )
    list_filter = ("status", "ended_by", "created_at")
    search_fields = ("user__username", "provider__username", "end_reason")
    inlines = [SessionBillingRecordInline]


@admin.register(SessionBillingRecord)
class SessionBillingRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "minute_number", "amount", "billed_at")
    list_filter = ("billed_at",)
    search_fields = ("session__id", "session__user__username")
