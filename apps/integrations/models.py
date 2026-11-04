"""Listings, sales channels, connections to outside services, the event outbox and API keys."""

import hashlib
import json
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.properties.models import Space
from apps.workspaces.models import Workspace, WorkspaceScopedModel

from . import crypto


class Connection(WorkspaceScopedModel):
    """One operator's account with an outside service. Secrets are stored encrypted."""

    class Provider(models.TextChoices):
        HOSTAWAY = "hostaway", _("Hostaway (Airbnb, Booking.com, Vrbo)")
        TELEGRAM = "telegram", _("Telegram bot")
        N8N = "n8n", _("n8n workflows")

    AUDIT_EXCLUDE = ("secret_encrypted", "settings", "webhook_token")

    provider = models.CharField(max_length=16, choices=Provider.choices)
    name = models.CharField(max_length=120, help_text=_("For example: Oceanway Hostaway account"))
    is_enabled = models.BooleanField(default=True)
    account_id = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("Hostaway: account ID. Telegram: channel, e.g. @oceanway_rooms. n8n: webhook URL."),
    )
    secret_encrypted = models.TextField(blank=True, editable=False)
    settings = models.JSONField(default=dict, blank=True, editable=False)
    webhook_token = models.CharField(max_length=64, unique=True, editable=False)
    last_success_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_error = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ["provider", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_provider_display()})"

    def save(self, *args, **kwargs):
        if not self.webhook_token:
            self.webhook_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    @property
    def secret(self):
        return crypto.decrypt(self.secret_encrypted)

    def set_secret(self, value):
        self.secret_encrypted = crypto.encrypt(value)

    @property
    def has_secret(self):
        return bool(self.secret_encrypted)

    def mark_ok(self):
        self.last_success_at = timezone.now()
        self.last_error = ""
        self.save(update_fields=["last_success_at", "last_error", "updated_at"])

    def mark_error(self, message):
        self.last_error = str(message)[:2000]
        self.save(update_fields=["last_error", "updated_at"])


class Listing(WorkspaceScopedModel):
    """The advert for one rentable space: one master text and photo set, sent to many channels."""

    class StayType(models.TextChoices):
        SHORT = "short", _("Short stays (nightly)")
        LONG = "long", _("Monthly / fixed term")
        BOTH = "both", _("Both")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        READY = "ready", _("Ready to post")
        PAUSED = "paused", _("Paused")

    space = models.ForeignKey(Space, on_delete=models.PROTECT, related_name="listings")
    title = models.CharField(max_length=120)
    summary = models.CharField(max_length=300, blank=True, help_text=_("One or two lines for the top of every post"))
    description = models.TextField(blank=True)
    amenities = models.TextField(blank=True, help_text=_("One per line: Wi-Fi, laundry, parking..."))
    stay_type = models.CharField(max_length=8, choices=StayType.choices, default=StayType.LONG)
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    nightly_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    available_from = models.DateField(null=True, blank=True)
    contact_line = models.CharField(
        max_length=200, blank=True, help_text=_("How people reach you, e.g. WhatsApp number")
    )
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["space__property__name", "space__name"]

    def __str__(self):
        return self.title

    def price_monthly(self):
        return self.monthly_price if self.monthly_price is not None else self.space.monthly_rent

    def price_nightly(self):
        return self.nightly_price if self.nightly_price is not None else self.space.nightly_rate

    def photos(self):
        from django.contrib.contenttypes.models import ContentType

        from apps.records.models import RecordFile

        ct = ContentType.objects.get_for_model(Listing)
        return RecordFile.objects.active().filter(
            workspace_id=self.workspace_id, content_type=ct, object_id=self.pk, kind=RecordFile.Kind.PHOTO
        )


class ListingChannel(WorkspaceScopedModel):
    """Where one listing is advertised, and how it is doing there."""

    class Channel(models.TextChoices):
        AIRBNB = "airbnb", _("Airbnb")
        BOOKING = "booking", _("Booking.com")
        VRBO = "vrbo", _("Vrbo")
        TELEGRAM = "telegram", _("Telegram channel")
        FACEBOOK_GROUP = "facebook_group", _("Facebook group")
        FACEBOOK_MARKETPLACE = "fb_marketplace", _("Facebook Marketplace")
        KIJIJI = "kijiji", _("Kijiji")
        CRAIGSLIST = "craigslist", _("Craigslist")
        ZUMPER = "zumper", _("Zumper / PadMapper")
        WEBSITE = "website", _("Own website")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        NOT_POSTED = "not_posted", _("Not posted")
        POSTED = "posted", _("Posted")
        NEEDS_UPDATE = "needs_update", _("Needs update")
        REMOVED = "removed", _("Removed")
        ERROR = "error", _("Error")

    # Channels this system can post to (or link to) on its own; the rest are posted by hand with the post kit.
    AUTOMATIC = {"telegram": "telegram", "airbnb": "hostaway", "booking": "hostaway", "vrbo": "hostaway"}

    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name="channels")
    channel = models.CharField(max_length=16, choices=Channel.choices)
    connection = models.ForeignKey(
        Connection, null=True, blank=True, on_delete=models.PROTECT, related_name="listing_channels"
    )
    target = models.CharField(
        max_length=200, blank=True, help_text=_("Group name or link for hand-posted channels, e.g. Vancouver Rooms")
    )
    external_id = models.CharField(
        max_length=120, blank=True, help_text=_("Hostaway listing ID, Telegram message ID or the channel's listing ID")
    )
    url = models.URLField(blank=True)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.NOT_POSTED)
    posted_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["listing", "channel"]

    def __str__(self):
        target = f" · {self.target}" if self.target else ""
        return f"{self.listing} on {self.get_channel_display()}{target}"

    @property
    def is_automatic(self):
        provider = self.AUTOMATIC.get(self.channel)
        return bool(provider and self.connection_id and self.connection.provider == provider)


class SyncLog(models.Model):
    """Every call to or from an outside service, kept for troubleshooting and to skip duplicates."""

    class Direction(models.TextChoices):
        IN = "in", _("Received")
        OUT = "out", _("Sent")

    class Status(models.TextChoices):
        OK = "ok", _("OK")
        SKIPPED = "skipped", _("Skipped")
        ERROR = "error", _("Error")

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="+")
    connection = models.ForeignKey(Connection, null=True, blank=True, on_delete=models.SET_NULL, related_name="logs")
    direction = models.CharField(max_length=3, choices=Direction.choices)
    kind = models.CharField(max_length=60)
    idempotency_key = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OK)
    message = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["connection", "idempotency_key"])]

    def __str__(self):
        return f"{self.kind} {self.status}"


class OutboundEvent(models.Model):
    """Outbox: business events waiting to be delivered to an n8n webhook, retried with backoff."""

    MAX_ATTEMPTS = 8

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="+")
    connection = models.ForeignKey(Connection, on_delete=models.CASCADE, related_name="outbound_events")
    event = models.CharField(max_length=60)
    payload = models.JSONField(default=dict)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["delivered_at", "failed_at", "next_attempt_at"])]

    def __str__(self):
        return f"{self.event} → {self.connection}"

    def body(self):
        return json.dumps(
            {"id": self.pk, "event": self.event, "workspace": self.workspace.slug, "data": self.payload},
            default=str,
            separators=(",", ":"),
        ).encode()


def _hash_key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class ApiKey(models.Model):
    """Key for machines (n8n, scripts) calling the REST API. Only a hash is stored."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=120)
    prefix = models.CharField(max_length=12, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    acting_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
        help_text=_("Changes made with this key are recorded under this user"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    @classmethod
    def issue(cls, workspace, name, acting_user):
        raw = "ros_" + secrets.token_urlsafe(32)
        key = cls.objects.create(
            workspace=workspace, name=name, acting_user=acting_user, prefix=raw[:12], key_hash=_hash_key(raw)
        )
        return key, raw

    @classmethod
    def lookup(cls, raw):
        return (
            cls.objects.select_related("workspace", "acting_user")
            .filter(key_hash=_hash_key(raw), revoked_at__isnull=True, workspace__is_active=True)
            .first()
        )


class JobRun(models.Model):
    """Marks that a daily job already ran for a workspace on a given local date."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="+")
    job = models.CharField(max_length=60)
    run_on = models.DateField()
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "job", "run_on"], name="jobrun_once_per_day")]

    def __str__(self):
        return f"{self.job} {self.run_on}"
