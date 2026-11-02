from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline

from apps.workspaces.admin_base import WorkspaceScopedAdmin

from .models import AuditEntry, Correspondence, Incident, RecordFile


class RecordFileInline(GenericTabularInline):
    """Attach files (slips, screenshots, letters, photos) to any record."""

    model = RecordFile
    extra = 1
    fields = ["kind", "file", "title", "taken_on"]


@admin.register(RecordFile)
class RecordFileAdmin(WorkspaceScopedAdmin):
    list_display = ["__str__", "kind", "content_type", "object_id", "created_at"]
    list_filter = ["kind", "content_type"]


@admin.register(Correspondence)
class CorrespondenceAdmin(WorkspaceScopedAdmin):
    list_display = [
        "occurred_at",
        "subject",
        "counterpart",
        "counterpart_name",
        "channel",
        "direction",
        "status",
        "deadline",
    ]
    list_filter = ["counterpart", "channel", "direction", "status"]
    search_fields = ["subject", "body", "counterpart_name"]
    date_hierarchy = "occurred_at"
    inlines = [RecordFileInline]


@admin.register(Incident)
class IncidentAdmin(WorkspaceScopedAdmin):
    list_display = ["occurred_at", "kind", "issue", "property", "tenancy", "follow_up_on"]
    list_filter = ["kind"]
    search_fields = ["issue", "description"]
    inlines = [RecordFileInline]


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    list_display = ["at", "action", "content_type", "object_id", "user"]
    list_filter = ["action", "content_type"]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.filter(workspace_id=getattr(request.workspace, "pk", None))

    def has_module_permission(self, request):
        m = request.membership
        return request.user.is_superuser or (m is not None and m.role in {"owner", "admin"})

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
