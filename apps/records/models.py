"""Files, correspondence and incidents. Everything is kept and linked to the record it belongs to."""

import uuid

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.landlords.models import Landlord, Lease
from apps.people.models import Person
from apps.properties.models import Property
from apps.tenancies.models import Tenancy
from apps.workspaces.models import WorkspaceScopedModel


def record_upload_path(instance, filename):
    return f"ws{instance.workspace_id}/{instance.kind}/{uuid.uuid4().hex}/{filename}"


class RecordFile(WorkspaceScopedModel):
    """A file attached to any record: payment slip, ID, agreement, chat screenshot, photo, letter."""

    class Kind(models.TextChoices):
        PAYMENT_SLIP = "payment_slip", _("Payment slip / receipt")
        ID_DOCUMENT = "id_document", _("ID document")
        AGREEMENT = "agreement", _("Agreement / contract")
        CHAT_SCREENSHOT = "chat_screenshot", _("Chat screenshot")
        PHOTO = "photo", _("Photo / video")
        LETTER = "letter", _("Letter / email")
        INVOICE = "invoice", _("Invoice")
        OTHER = "other", _("Other")

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    attached_to = GenericForeignKey("content_type", "object_id")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    file = models.FileField(upload_to=record_upload_path)
    title = models.CharField(max_length=200, blank=True)
    taken_on = models.DateField(null=True, blank=True)
    uploaded_by = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    retain_until = models.DateField(
        null=True, blank=True, help_text=_("Only set when privacy law limits retention, e.g. ID images")
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return self.title or self.file.name


class Correspondence(WorkspaceScopedModel):
    """Emails, letters, chats and calls with landlords, cities, government offices or stratas."""

    class Counterpart(models.TextChoices):
        LANDLORD = "landlord", _("Landlord")
        CITY = "city", _("City / municipality")
        GOVERNMENT = "government", _("Government office")
        STRATA = "strata", _("Strata / building")
        TENANT = "tenant", _("Tenant")
        OTHER = "other", _("Other")

    class Channel(models.TextChoices):
        EMAIL = "email", _("Email")
        CHAT = "chat", _("Chat")
        CALL = "call", _("Phone call")
        LETTER = "letter", _("Letter")
        IN_PERSON = "in_person", _("In person")

    class Direction(models.TextChoices):
        IN = "in", _("Received")
        OUT = "out", _("Sent")

    class Status(models.TextChoices):
        NONE = "none", _("No reply needed")
        NEEDS_REPLY = "needs_reply", _("Needs reply")
        REPLIED = "replied", _("Replied")
        WAITING = "waiting", _("Waiting for them")

    counterpart = models.CharField(max_length=12, choices=Counterpart.choices)
    landlord = models.ForeignKey(
        Landlord, null=True, blank=True, on_delete=models.PROTECT, related_name="correspondence"
    )
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.PROTECT, related_name="correspondence")
    property = models.ForeignKey(
        Property, null=True, blank=True, on_delete=models.PROTECT, related_name="correspondence"
    )
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.PROTECT, related_name="correspondence")
    counterpart_name = models.CharField(max_length=200, blank=True)
    channel = models.CharField(max_length=10, choices=Channel.choices)
    direction = models.CharField(max_length=3, choices=Direction.choices)
    occurred_at = models.DateTimeField()
    subject = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    deadline = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NONE)
    external_message_id = models.CharField(max_length=255, blank=True, help_text=_("Email Message-ID, for dedupe"))

    class Meta:
        ordering = ["-occurred_at"]
        verbose_name_plural = "correspondence"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "external_message_id"],
                condition=~models.Q(external_message_id=""),
                name="unique_external_message",
            )
        ]

    def __str__(self):
        return self.subject


class Incident(WorkspaceScopedModel):
    """Reminders, warnings and incidents, kept separate with their evidence."""

    class Kind(models.TextChoices):
        REMINDER = "reminder", _("Reminder")
        WARNING = "warning", _("Warning")
        INCIDENT = "incident", _("Incident")

    kind = models.CharField(max_length=10, choices=Kind.choices)
    tenancy = models.ForeignKey(Tenancy, null=True, blank=True, on_delete=models.PROTECT, related_name="incidents")
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.PROTECT, related_name="incidents")
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="incidents")
    occurred_at = models.DateTimeField()
    issue = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    response = models.TextField(blank=True)
    outcome = models.TextField(blank=True)
    follow_up_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.issue}"


class AuditEntry(models.Model):
    """Who changed what and when. Written by signals; never edited."""

    workspace_id = models.BigIntegerField(db_index=True)
    user = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=10)
    changes = models.JSONField(default=dict)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
        verbose_name_plural = "audit entries"

    def __str__(self):
        return f"{self.action} {self.content_type} #{self.object_id}"
