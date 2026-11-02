"""Tenancies (from one-night stays to one-year terms), payments, deposits and agreements."""

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Func, Q
from django.utils.translation import gettext_lazy as _

from apps.landlords.models import Lease
from apps.people.models import Person
from apps.properties.models import Space
from apps.workspaces.models import WorkspaceScopedModel


class DateRange(Func):
    function = "daterange"
    output_field = models.Field()  # only used inside the exclusion constraint


BLOCKING_STATUSES = ("reserved", "active", "notice_given")


class Tenancy(WorkspaceScopedModel):
    class Type(models.TextChoices):
        SHORT_STAY = "short_stay", _("Short stay (nightly)")
        FIXED_TERM = "fixed_term", _("Fixed term")
        PERIODIC = "periodic", _("Month to month")

    class Status(models.TextChoices):
        RESERVED = "reserved", _("Reserved")
        ACTIVE = "active", _("Active")
        NOTICE_GIVEN = "notice_given", _("Notice given")
        ENDED = "ended", _("Ended")
        CANCELLED = "cancelled", _("Cancelled")

    space = models.ForeignKey(Space, on_delete=models.PROTECT, related_name="tenancies")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="tenancies")
    type = models.CharField(max_length=12, choices=Type.choices)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.RESERVED)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text=_("Check-out date; empty for open-ended"))
    rent_amount = models.DecimalField(max_digits=12, decimal_places=2)
    rent_period = models.CharField(
        max_length=8, choices=[("night", _("per night")), ("month", _("per month")), ("total", _("total"))]
    )
    currency = models.CharField(max_length=3)
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    source_channel = models.CharField(max_length=40, blank=True, help_text=_("airbnb, booking, direct, facebook..."))
    external_reference = models.CharField(max_length=120, blank=True)
    notice_date = models.DateField(null=True, blank=True)
    actual_move_out = models.DateField(null=True, blank=True)
    deposit_refunded_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date"]
        verbose_name_plural = "tenancies"
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gt=models.F("start_date")),
                name="tenancy_end_after_start",
            ),
            # No two live tenancies may overlap on the same space (no double booking).
            ExclusionConstraint(
                name="tenancy_no_overlap",
                expressions=[
                    ("space", RangeOperators.EQUAL),
                    (DateRange("start_date", "end_date"), RangeOperators.OVERLAPS),
                ],
                condition=Q(status__in=BLOCKING_STATUSES, archived_at__isnull=True),
            ),
        ]

    def __str__(self):
        return f"{self.person} in {self.space} from {self.start_date}"

    def get_constraints(self):
        """Skip the exclusion constraint during form validation; ``clean()`` runs the same check
        (and also covers parent and child spaces). The database still enforces it on save."""
        return [
            (model, [c for c in constraints if c.name != "tenancy_no_overlap"])
            for model, constraints in super().get_constraints()
        ]

    def overlapping(self):
        """Live tenancies on this space, its parent or its children that overlap these dates.

        The database constraint covers the same space; this also catches a room
        rented while one of its beds is booked, and the reverse.
        """
        related_spaces = [self.space_id]
        if self.space.parent_id:
            related_spaces.append(self.space.parent_id)
        related_spaces += list(self.space.children.values_list("id", flat=True))
        qs = Tenancy.objects.active().filter(space_id__in=related_spaces, status__in=BLOCKING_STATUSES)
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        if self.end_date:
            qs = qs.filter(start_date__lt=self.end_date)
        return qs.filter(Q(end_date__isnull=True) | Q(end_date__gt=self.start_date))

    def clean(self):
        if self.end_date and self.end_date <= self.start_date:
            raise ValidationError({"end_date": _("Check-out must be after check-in.")})
        if self.space_id and self.status in BLOCKING_STATUSES and self.overlapping().exists():
            raise ValidationError(_("These dates overlap another booking on this space or a space inside it."))


class Payment(WorkspaceScopedModel):
    """Any money in (from a tenant) or out (to a landlord), with its slip attached as a Record file."""

    class Direction(models.TextChoices):
        IN = "in", _("Received")
        OUT = "out", _("Paid")

    class Purpose(models.TextChoices):
        RENT = "rent", _("Rent")
        DEPOSIT = "deposit", _("Deposit")
        GUEST_FEE = "guest_fee", _("Guest fee")
        CLEANING = "cleaning", _("Cleaning charge")
        DAMAGE = "damage", _("Damage charge")
        CANCELLATION = "cancellation", _("Cancellation fee")
        REFUND = "refund", _("Refund")
        LANDLORD_RENT = "landlord_rent", _("Rent to landlord")
        LANDLORD_DEPOSIT = "landlord_deposit", _("Deposit to landlord")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending review")
        CONFIRMED = "confirmed", _("Confirmed")
        REJECTED = "rejected", _("Rejected")

    direction = models.CharField(max_length=3, choices=Direction.choices)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    tenancy = models.ForeignKey(Tenancy, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    period = models.DateField(null=True, blank=True, help_text=_("First day of the month this payment covers"))
    paid_on = models.DateField()
    method = models.CharField(max_length=40, blank=True, help_text=_("e-transfer, cash, card, bank transfer..."))
    reference = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    confirmed_by = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-paid_on"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
            models.CheckConstraint(
                condition=Q(tenancy__isnull=False, lease__isnull=True) | Q(tenancy__isnull=True, lease__isnull=False),
                name="payment_links_tenancy_or_lease",
            ),
        ]

    def __str__(self):
        return f"{self.get_direction_display()} {self.amount} {self.currency} ({self.get_purpose_display()})"


class DepositDeduction(WorkspaceScopedModel):
    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="deposit_deductions")
    reason = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.reason}: {self.amount}"


class Agreement(WorkspaceScopedModel):
    class Kind(models.TextChoices):
        RENTAL = "rental", _("Rental agreement")
        HOUSE_RULES = "house_rules", _("House rules")
        ADDENDUM = "addendum", _("Addendum")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SENT = "sent", _("Sent")
        VIEWED = "viewed", _("Viewed")
        SIGNED = "signed", _("Signed")
        VOID = "void", _("Void")

    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="agreements")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    sent_at = models.DateTimeField(null=True, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.get_kind_display()} for {self.tenancy} ({self.status})"
