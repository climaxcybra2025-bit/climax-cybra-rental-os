from django.contrib import admin
from django.utils.html import format_html

from apps.workspaces.admin_base import WorkspaceScopedAdmin

from .models import Landlord, LandlordRepresentative, Lease


@admin.register(Landlord)
class LandlordAdmin(WorkspaceScopedAdmin):
    files_inline = True
    list_display = ["name", "company", "email", "phone"]
    search_fields = ["name", "company", "email", "phone"]


@admin.register(LandlordRepresentative)
class LandlordRepresentativeAdmin(WorkspaceScopedAdmin):
    list_display = ["name", "landlord", "role", "phone", "email", "handles_repairs"]
    search_fields = ["name", "landlord__name"]


@admin.register(Lease)
class LeaseAdmin(WorkspaceScopedAdmin):
    files_inline = True
    list_display = [
        "property",
        "landlord",
        "start_date",
        "end_date",
        "expiry",
        "monthly_rent",
        "currency",
        "sublet_consent",
        "renewal_decision",
    ]
    list_filter = ["sublet_consent", "renewal_decision"]
    search_fields = ["property__name", "landlord__name"]
    date_hierarchy = "end_date"

    @admin.display(description="Days to expiry", ordering="end_date")
    def expiry(self, obj):
        days = obj.days_to_expiry()
        if days is None:
            return "open-ended"
        colour = "#b42318" if days <= 60 else "inherit"
        return format_html('<span style="color:{}">{}</span>', colour, days)
