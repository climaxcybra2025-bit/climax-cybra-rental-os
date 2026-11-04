"""Day-to-day operations: maintenance tickets, inspections and the cleaning roster."""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.people.models import Person
from apps.properties.models import Property, Space
from apps.tenancies.models import Tenancy
from apps.workspaces.models import WorkspaceScopedModel


class Ticket(WorkspaceScopedModel):
    class Category(models.TextChoices):
        PLUMBING = "plumbing", _("Plumbing")
        ELECTRICAL = "electrical", _("Electrical")
        HEATING = "heating", _("Heating")
        LAUNDRY = "laundry", _("Laundry")
        APPLIANCE = "appliance", _("Appliance")
        INTERNET = "internet", _("Internet")
        FURNITURE = "furniture", _("Furniture")
        CLEANING = "cleaning", _("Cleaning")
        SAFETY = "safety", _("Safety")
        NEIGHBOUR = "neighbour", _("Housemate / noise")
        OTHER = "other", _("Other")

    class Priority(models.TextChoices):
        URGENT = "urgent", _("Urgent")
        NORMAL = "normal", _("Not urgent")

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        SCHEDULED = "scheduled", _("Scheduled")
        IN_PROGRESS = "in_progress", _("In progress")
        COMPLETED = "completed", _("Completed")
        CLOSED = "closed", _("Closed")

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="tickets")
    space = models.ForeignKey(Space, null=True, blank=True, on_delete=models.PROTECT, related_name="tickets")
    reported_by = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets")
    category = models.CharField(max_length=12, choices=Category.choices)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    landlord_responsibility = models.BooleanField(default=False)
    landlord_notified_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tickets"
    )
    appointment_at = models.DateTimeField(null=True, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.priority}] {self.title}"


class Inspection(WorkspaceScopedModel):
    class Kind(models.TextChoices):
        MOVE_IN = "move_in", _("Move-in")
        MOVE_OUT = "move_out", _("Move-out")
        ROUTINE = "routine", _("Routine")
        TURNOVER = "turnover", _("Turnover cleaning check")

    space = models.ForeignKey(Space, on_delete=models.PROTECT, related_name="inspections")
    tenancy = models.ForeignKey(Tenancy, null=True, blank=True, on_delete=models.PROTECT, related_name="inspections")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    inspected_on = models.DateField()
    # {"walls": {"ok": true, "notes": ""}, "floor": {...}, ...}
    checklist = models.JSONField(default=dict, blank=True)
    summary = models.TextField(blank=True)

    def __str__(self):
        return f"{self.get_kind_display()} {self.space} {self.inspected_on}"


class CleaningDuty(WorkspaceScopedModel):
    """One roommate's cleaning turn for one day, with photo proof attached as Record files."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", _("Scheduled")
        REMINDER_SENT = "reminder_sent", _("Reminder sent")
        PROOF_RECEIVED = "proof_received", _("Proof received")
        COMPLETED = "completed", _("Completed")
        SWAPPED = "swapped", _("Swapped")
        WORKER_ASSIGNED = "worker_assigned", _("Worker assigned")
        MISSED = "missed", _("Missed")

    class Kind(models.TextChoices):
        ROSTER = "roster", _("Roommate roster")
        TURNOVER = "turnover", _("Turnover after checkout")

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.ROSTER)
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="cleaning_duties")
    space = models.ForeignKey(
        Space,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cleaning_duties",
        help_text=_("For turnover cleans: the room, bed or unit to get ready"),
    )
    tenancy = models.ForeignKey(
        Tenancy,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="turnover_cleans",
        help_text=_("The stay that checked out"),
    )
    person = models.ForeignKey(
        Person,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="cleaning_duties",
        help_text=_("Roommate on the roster, or the cleaner assigned"),
    )
    due_date = models.DateField()
    areas = models.CharField(max_length=200, default="Kitchen, bathroom, shared areas, garbage")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED)
    proof_received_at = models.DateTimeField(null=True, blank=True)
    charge_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["due_date"]
        verbose_name_plural = "cleaning duties"

    def __str__(self):
        who = self.person or "Unassigned"
        where = self.space.name if self.space_id else self.property
        return f"{who} cleans {where} on {self.due_date}"
