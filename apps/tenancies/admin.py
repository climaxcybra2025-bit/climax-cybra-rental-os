from django.contrib import admin
from django.utils import timezone

from apps.workspaces.admin_base import WorkspaceScopedAdmin

from .models import Agreement, DepositDeduction, Payment, Tenancy


class DeductionInline(admin.TabularInline):
    model = DepositDeduction
    extra = 0
    fields = ["reason", "amount", "notes"]


@admin.register(Tenancy)
class TenancyAdmin(WorkspaceScopedAdmin):
    files_inline = True
    list_display = [
        "person",
        "space",
        "type",
        "status",
        "start_date",
        "end_date",
        "rent_amount",
        "rent_period",
        "currency",
        "source_channel",
    ]
    list_filter = ["type", "status", "source_channel"]
    search_fields = ["person__first_name", "person__last_name", "space__name", "space__property__name"]
    date_hierarchy = "start_date"
    inlines = [DeductionInline]


@admin.register(Payment)
class PaymentAdmin(WorkspaceScopedAdmin):
    files_inline = True
    list_display = ["paid_on", "direction", "purpose", "amount", "currency", "tenancy", "lease", "method", "status"]
    list_filter = ["direction", "purpose", "status", "method"]
    date_hierarchy = "paid_on"
    actions = ["confirm"]
    readonly_fields = ["confirmed_by", "confirmed_at"]

    @admin.action(description="Confirm selected payments")
    def confirm(self, request, queryset):
        n = 0
        for p in queryset.filter(status=Payment.Status.PENDING):
            p.status = Payment.Status.CONFIRMED
            p.confirmed_by = request.user
            p.confirmed_at = timezone.now()
            p.save()
            n += 1
        self.message_user(request, f"{n} payment(s) confirmed.")


@admin.register(Agreement)
class AgreementAdmin(WorkspaceScopedAdmin):
    files_inline = True
    list_display = ["tenancy", "kind", "status", "sent_at", "signed_at"]
    list_filter = ["kind", "status"]
