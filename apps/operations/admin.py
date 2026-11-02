from django.contrib import admin

from apps.workspaces.admin_base import WorkspaceScopedAdmin

from .models import CleaningDuty, Inspection, Ticket


@admin.register(Ticket)
class TicketAdmin(WorkspaceScopedAdmin):
    files_inline = True
    list_display = [
        "title",
        "property",
        "space",
        "priority",
        "status",
        "category",
        "landlord_responsibility",
        "created_at",
    ]
    list_filter = ["priority", "status", "category", "landlord_responsibility"]
    search_fields = ["title", "description", "property__name"]
    list_editable = ["status"]


@admin.register(Inspection)
class InspectionAdmin(WorkspaceScopedAdmin):
    files_inline = True
    list_display = ["space", "kind", "inspected_on", "tenancy"]
    list_filter = ["kind"]


@admin.register(CleaningDuty)
class CleaningDutyAdmin(WorkspaceScopedAdmin):
    files_inline = True
    list_display = ["due_date", "property", "person", "status", "charge_amount"]
    list_filter = ["status", "property"]
    date_hierarchy = "due_date"
    list_editable = ["status"]
