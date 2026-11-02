"""Properties and the spaces rented inside them (whole unit, room, bed, couch)."""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.workspaces.models import WorkspaceScopedModel


class Property(WorkspaceScopedModel):
    class HouseType(models.TextChoices):
        MIXED = "mixed", _("Mixed")
        FEMALE = "female", _("Female only")
        MALE = "male", _("Male only")

    class StayPolicy(models.TextChoices):
        ANY = "any", _("Short and long stays")
        LONG_ONLY = "long_only", _("Long stays only")
        SHORT_ONLY = "short_only", _("Short stays only")

    name = models.CharField(max_length=200)
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120)
    region = models.CharField(max_length=120, blank=True, help_text=_("Province, state or prefecture"))
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2)
    time_zone = models.CharField(max_length=64)
    currency = models.CharField(max_length=3)
    house_type = models.CharField(max_length=10, choices=HouseType.choices, default=HouseType.MIXED)
    stay_policy = models.CharField(max_length=12, choices=StayPolicy.choices, default=StayPolicy.ANY)
    # Short-stay compliance fields; required by some jurisdictions only.
    str_registration_number = models.CharField(max_length=64, blank=True)
    business_licence_number = models.CharField(max_length=64, blank=True)
    business_licence_expiry = models.DateField(null=True, blank=True)
    # Wi-Fi, laundry hours, parking, lockbox, bathroom assignment, garbage day, etc.
    house_info = models.JSONField(default=dict, blank=True)
    house_rules = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "properties"

    def __str__(self):
        return self.name


class Space(WorkspaceScopedModel):
    """Anything that can be rented: a whole unit, a room, a bed in a room, a couch."""

    class Kind(models.TextChoices):
        UNIT = "unit", _("Whole unit")
        ROOM = "room", _("Room")
        BED = "bed", _("Bed")
        COUCH = "couch", _("Couch")
        OTHER = "other", _("Other space")

    class Bathroom(models.TextChoices):
        PRIVATE = "private", _("Private / ensuite")
        SHARED = "shared", _("Shared")

    class Status(models.TextChoices):
        AVAILABLE = "available", _("Available")
        ADVERTISED = "advertised", _("Advertised")
        ON_HOLD = "on_hold", _("On hold")
        RENTED = "rented", _("Rented")
        TURNOVER = "turnover", _("Turnover / cleaning")
        BLOCKED = "blocked", _("Blocked")

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="spaces")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=120)
    floor = models.CharField(max_length=32, blank=True)
    bathroom = models.CharField(max_length=10, choices=Bathroom.choices, blank=True)
    furnished = models.BooleanField(default=True)
    max_occupants = models.PositiveSmallIntegerField(default=1)
    monthly_rent = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    nightly_rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    deposit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    min_stay_nights = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.AVAILABLE)
    furniture = models.TextField(blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["property__name", "name"]

    def __str__(self):
        return f"{self.property} / {self.name}"

    def get_currency(self):
        return self.property.currency

    def clean(self):
        if self.parent_id:
            if self.parent.property_id != self.property_id:
                raise ValidationError({"parent": _("Parent space must be in the same property.")})
            if self.parent_id == self.pk:
                raise ValidationError({"parent": _("A space cannot be its own parent.")})
        if self.property_id and self.workspace_id and self.property.workspace_id != self.workspace_id:
            raise ValidationError(_("Property belongs to another workspace."))
