"""Workspaces: one per operator company that subscribes to the platform.

Every business record belongs to exactly one workspace. Records are never
hard-deleted: ``delete()`` archives them so the full history stays available
for disputes and audits.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Workspace(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    country = models.CharField(max_length=2, help_text=_("ISO 3166-1 alpha-2, e.g. CA, US, JP"))
    base_currency = models.CharField(max_length=3, help_text=_("ISO 4217, e.g. CAD, USD, JPY"))
    time_zone = models.CharField(max_length=64, default="UTC")
    language = models.CharField(max_length=8, default="en")
    rule_pack = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Jurisdiction rule pack, e.g. ca-bc. Drives deposit, notice and short-stay checks."),
    )
    emergency_phone = models.CharField(max_length=32, blank=True, help_text=_("After-hours number shown to tenants"))
    urgent_ticket_emails = models.TextField(
        blank=True, help_text=_("Comma-separated emails that receive urgent tickets")
    )
    # Operator-controlled fees and policies, e.g. {"cancellation_fee": "1000", "guest_fee_per_night": "40"}
    policies = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def urgent_recipients(self):
        return [e.strip() for e in self.urgent_ticket_emails.split(",") if e.strip()]


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", _("Owner")
        ADMIN = "admin", _("Admin")
        PARTNER = "partner", _("Partner")
        TEAM = "team", _("Team member")
        CLEANER = "cleaner", _("Cleaner")
        TENANT = "tenant", _("Tenant")

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=16, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "user"], name="unique_membership")]

    def __str__(self):
        return f"{self.user} @ {self.workspace} ({self.role})"

    @property
    def is_staff_role(self):
        return self.role in {self.Role.OWNER, self.Role.ADMIN, self.Role.PARTNER, self.Role.TEAM}


class ActiveQuerySet(models.QuerySet):
    def active(self):
        return self.filter(archived_at__isnull=True)

    def for_workspace(self, workspace):
        return self.filter(workspace=workspace)


class WorkspaceScopedModel(models.Model):
    """Base for every business record: scoped to a workspace, archived instead of deleted."""

    workspace = models.ForeignKey(Workspace, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = ActiveQuerySet.as_manager()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        """Archive instead of deleting, so no record is ever lost."""
        self.archived_at = timezone.now()
        self.save(update_fields=["archived_at", "updated_at"])
        return (0, {})

    def hard_delete(self, using=None, keep_parents=False):
        return super().delete(using=using, keep_parents=keep_parents)


class Money(models.Model):
    """Mixin for records with one amount; every amount carries its own currency."""

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)

    class Meta:
        abstract = True
