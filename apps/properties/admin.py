from django.contrib import admin

from apps.workspaces.admin_base import WorkspaceScopedAdmin

from .models import Property, Space


class SpaceInline(admin.TabularInline):
    model = Space
    extra = 0
    fields = ["kind", "name", "parent", "bathroom", "monthly_rent", "nightly_rate", "deposit", "status"]
    show_change_link = True

    def get_queryset(self, request):
        return super().get_queryset(request).filter(archived_at__isnull=True)


@admin.register(Property)
class PropertyAdmin(WorkspaceScopedAdmin):
    files_inline = True
    list_display = ["name", "city", "country", "house_type", "stay_policy", "space_count"]
    list_filter = ["country", "house_type", "stay_policy"]
    search_fields = ["name", "address_line1", "city"]
    inlines = [SpaceInline]

    @admin.display(description="Spaces")
    def space_count(self, obj):
        return obj.spaces.filter(archived_at__isnull=True).count()


@admin.register(Space)
class SpaceAdmin(WorkspaceScopedAdmin):
    files_inline = True
    list_display = ["name", "property", "kind", "bathroom", "monthly_rent", "nightly_rate", "status"]
    list_filter = ["kind", "status", "bathroom"]
    search_fields = ["name", "property__name"]
    list_editable = ["status"]
