"""Landlords, their representatives, and the master leases the operator signs with them."""

from datetime import date

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.properties.models import Property
from apps.workspaces.models import WorkspaceScopedModel


class Landlord(WorkspaceScopedModel):
    name = models.CharField(max_length=200)
    company = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    address = models.TextField(blank=True)
    payment_details = models.TextField(blank=True, help_text=_("How rent is paid to this landlord"))
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LandlordRepresentative(WorkspaceScopedModel):
    """Person who acts for the landlord, e.g. the property manager to call for repairs."""

    landlord = models.ForeignKey(Landlord, on_delete=models.PROTECT, related_name="representatives")
    name = models.CharField(max_length=200)
    role = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    handles_repairs = models.BooleanField(default=True)
    handles_inspections = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} ({self.landlord})"


class Lease(WorkspaceScopedModel):
    """Master lease between the operator and a landlord."""

    class RenewalDecision(models.TextChoices):
        PENDING = "", _("Not decided")
        RENEW = "renew", _("Renew on current terms")
        NEGOTIATE = "negotiate", _("Negotiate")
        EXTEND = "extend", _("Extend / new agreement")
        DO_NOT_RENEW = "do_not_renew", _("Do not renew")
        NEED_TIME = "need_time", _("Need more time")

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="leases")
    landlord = models.ForeignKey(Landlord, on_delete=models.PROTECT, related_name="leases")
    representative = models.ForeignKey(
        LandlordRepresentative, null=True, blank=True, on_delete=models.SET_NULL, related_name="leases"
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text=_("Leave empty for month-to-month"))
    monthly_rent = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    deposit_paid = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    rent_due_day = models.PositiveSmallIntegerField(default=1)
    notice_period_days = models.PositiveIntegerField(null=True, blank=True)
    sublet_consent = models.BooleanField(default=False, help_text=_("Written consent to sublet is on file"))
    renewal_decision = models.CharField(max_length=16, choices=RenewalDecision.choices, blank=True, default="")
    renewal_follow_up = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["end_date"]

    def __str__(self):
        return f"Lease {self.property} with {self.landlord}"

    def days_to_expiry(self, today=None):
        if not self.end_date:
            return None
        return (self.end_date - (today or date.today())).days
