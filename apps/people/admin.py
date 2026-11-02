from django.contrib import admin

from apps.workspaces.admin_base import WorkspaceScopedAdmin

from .models import Person


@admin.register(Person)
class PersonAdmin(WorkspaceScopedAdmin):
    files_inline = True
    list_display = ["__str__", "kind", "stage", "phone", "email", "source"]
    list_filter = ["kind", "stage", "source"]
    search_fields = ["first_name", "last_name", "email", "phone"]
    list_editable = ["stage"]
