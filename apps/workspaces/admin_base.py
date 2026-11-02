from django.contrib import admin


class WorkspaceScopedAdmin(admin.ModelAdmin):
    """Back-office admin that only shows the signed-in user's workspace.

    Superusers (ClimaxCybra platform admins) see every workspace. Everyone
    else sees and creates records in their current workspace only, and
    foreign-key dropdowns are limited to that workspace.
    """

    list_per_page = 50

    def _is_platform_admin(self, request):
        return request.user.is_superuser

    files_inline = False

    def get_inlines(self, request, obj):
        inlines = list(super().get_inlines(request, obj))
        if self.files_inline:
            from apps.records.admin import RecordFileInline

            inlines.append(RecordFileInline)
        return inlines

    def get_queryset(self, request):
        qs = super().get_queryset(request).filter(archived_at__isnull=True)
        if self._is_platform_admin(request):
            return qs
        return qs.filter(workspace=request.workspace)

    def get_exclude(self, request, obj=None):
        excluded = list(super().get_exclude(request, obj) or [])
        if not self._is_platform_admin(request):
            excluded.append("workspace")
        return excluded

    def get_list_filter(self, request):
        filters = list(super().get_list_filter(request))
        if self._is_platform_admin(request):
            filters.insert(0, "workspace")
        return filters

    def save_model(self, request, obj, form, change):
        if not change and not obj.workspace_id:
            obj.workspace = request.workspace
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        """Inline rows (spaces, deductions, attached files) inherit the parent's workspace."""
        for obj in formset.save(commit=False):
            if hasattr(obj, "workspace_id") and not obj.workspace_id:
                obj.workspace_id = form.instance.workspace_id
            if hasattr(obj, "uploaded_by_id") and not obj.uploaded_by_id:
                obj.uploaded_by = request.user
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        model = db_field.related_model
        if not self._is_platform_admin(request) and any(f.name == "workspace" for f in model._meta.fields):
            kwargs["queryset"] = model._default_manager.filter(workspace=request.workspace, archived_at__isnull=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_module_permission(self, request):
        return self._is_platform_admin(request) or (request.membership is not None and request.membership.is_staff_role)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        # "Delete" archives the record (see WorkspaceScopedModel.delete).
        return self.has_module_permission(request)
