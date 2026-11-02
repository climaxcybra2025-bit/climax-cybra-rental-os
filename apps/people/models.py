"""People the operator deals with: leads, tenants, guests, cleaners, technicians."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.workspaces.models import WorkspaceScopedModel


class Person(WorkspaceScopedModel):
    class Stage(models.TextChoices):
        NEW_INQUIRY = "new_inquiry", _("New inquiry")
        PRE_SCREENING = "pre_screening", _("Pre-screening")
        PRE_SCREENING_PASSED = "pre_screening_passed", _("Pre-screening passed")
        APPLICATION_PENDING = "application_pending", _("Application pending")
        DOCUMENTS_PENDING = "documents_pending", _("Documents pending")
        DEPOSIT_PENDING = "deposit_pending", _("Deposit pending")
        AGREEMENT_PENDING = "agreement_pending", _("Agreement pending")
        READY_FOR_CHECK_IN = "ready_for_check_in", _("Ready for check-in")
        ACTIVE = "active", _("Active tenant")
        NOTICE_GIVEN = "notice_given", _("Notice given")
        MOVED_OUT = "moved_out", _("Moved out")
        DEPOSIT_REVIEW = "deposit_review", _("Deposit review")
        CLOSED = "closed", _("Closed")
        DECLINED = "declined", _("Declined")

    class Kind(models.TextChoices):
        RENTER = "renter", _("Lead / tenant / guest")
        CLEANER = "cleaner", _("Cleaner")
        TECHNICIAN = "technician", _("Technician / contractor")
        OFFICIAL = "official", _("City or government contact")
        OTHER = "other", _("Other")

    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.RENTER)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="people",
        help_text=_("Login for the tenant portal"),
    )
    first_name = models.CharField(max_length=120)
    last_name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    preferred_language = models.CharField(max_length=8, blank=True)
    stage = models.CharField(max_length=24, choices=Stage.choices, default=Stage.NEW_INQUIRY)
    source = models.CharField(max_length=64, blank=True, help_text=_("Airbnb, Booking.com, Facebook, referral..."))
    occupation = models.CharField(max_length=200, blank=True)
    current_address = models.TextField(blank=True)
    emergency_contact = models.TextField(blank=True)
    references = models.TextField(blank=True)
    id_verified_on = models.DateField(null=True, blank=True, help_text=_("Kept even after the ID image is purged"))
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["first_name", "last_name"]
        verbose_name_plural = "people"

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()
