from django.contrib import admin

from .models import Membership, Workspace


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["user"]


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ["name", "country", "base_currency", "rule_pack", "is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ["name"]}
    inlines = [MembershipInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(pk=getattr(request.workspace, "pk", None))

    def has_module_permission(self, request):
        m = request.membership
        return request.user.is_superuser or (m is not None and m.role in {"owner", "admin"})

    has_view_permission = has_change_permission = lambda self, request, obj=None: self.has_module_permission(request)

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
