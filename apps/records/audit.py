"""Audit trail: every create, change and archive of a business record is logged."""

import contextvars

from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.workspaces.models import WorkspaceScopedModel

current_user = contextvars.ContextVar("current_user", default=None)


class AuditUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        token = current_user.set(user)
        try:
            return self.get_response(request)
        finally:
            current_user.reset(token)


def _snapshot(instance):
    data = {}
    skip = {"created_at", "updated_at", *getattr(instance, "AUDIT_EXCLUDE", ())}
    for field in instance._meta.concrete_fields:
        if field.name in skip:
            continue
        value = getattr(instance, field.attname)
        data[field.attname] = value if isinstance(value, (int, float, bool, type(None))) else str(value)
    return data


@receiver(pre_save)
def remember_previous(sender, instance, **kwargs):
    if not isinstance(instance, WorkspaceScopedModel) or not instance.pk:
        return
    previous = sender._default_manager.filter(pk=instance.pk).first()
    instance._audit_before = _snapshot(previous) if previous else None


@receiver(post_save)
def write_audit(sender, instance, created, **kwargs):
    if not isinstance(instance, WorkspaceScopedModel) or kwargs.get("raw"):
        return
    from .models import AuditEntry

    after = _snapshot(instance)
    before = getattr(instance, "_audit_before", None) or {}
    if created:
        action, changes = "create", after
    else:
        changes = {k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}
        if not changes:
            return
        action = "archive" if "archived_at" in changes and instance.archived_at else "update"
    AuditEntry.objects.create(
        workspace_id=instance.workspace_id,
        user=current_user.get(),
        content_type=ContentType.objects.get_for_model(sender),
        object_id=instance.pk,
        action=action,
        changes=changes,
    )
