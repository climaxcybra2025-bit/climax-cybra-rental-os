"""Screen definitions for each record type.

Each Resource says how its list, form and detail page look. The generic views
in ``views.py`` read these definitions, so adding a field to a screen is a
one-line change here.
"""

from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _

from apps.integrations.models import Connection, Listing, ListingChannel
from apps.landlords.models import Landlord, LandlordRepresentative, Lease
from apps.operations.models import CleaningDuty, Inspection, Ticket
from apps.people.models import Person
from apps.properties.models import Property, Space
from apps.records.models import Correspondence, Incident, RecordFile
from apps.tenancies.models import Agreement, DepositDeduction, Payment, Tenancy

from .forms import ConnectionForm, scoped_form


class _Computed:
    do_not_call_in_templates = True

    def __init__(self, fn):
        self.fn = fn

    def __call__(self, obj):
        return self.fn(obj)


@dataclass
class Related:
    title: str
    resource: str
    fk: str  # field on the related model that points at the parent record


@dataclass
class Resource:
    slug: str
    model: type
    title: str
    singular: str
    columns: list  # (label, accessor) pairs; accessor is a dotted path or a callable
    form_fields: list
    search: list = field(default_factory=list)
    filters: list = field(default_factory=list)  # choice fields offered as filter chips
    detail_fields: list = field(default_factory=list)
    related: list = field(default_factory=list)
    attachment_kind: str = RecordFile.Kind.OTHER
    status_field: str = ""
    nav: bool = True
    icon: str = ""
    form_class: type = None
    admin_only: bool = False

    def __post_init__(self):
        # Templates would call a bare function with no arguments; wrap computed columns so they are passed through.
        self.columns = [(label, _Computed(acc) if callable(acc) else acc) for label, acc in self.columns]
        if self.form_class is None:
            self.form_class = scoped_form(self.model, self.form_fields, attachment_kind=self.attachment_kind)
        if not self.detail_fields:
            self.detail_fields = self.form_fields


def _days_left(lease):
    days = lease.days_to_expiry()
    return "—" if days is None else days


RESOURCES = [
    Resource(
        slug="properties",
        model=Property,
        title=_("Properties"),
        singular=_("Property"),
        icon="home",
        columns=[
            (_("Name"), "name"),
            (_("City"), "city"),
            (_("Country"), "country"),
            (_("House type"), "house_type"),
            (_("Stays"), "stay_policy"),
            (_("Spaces"), lambda p: p.spaces.filter(archived_at__isnull=True).count()),
        ],
        form_fields=[
            "name",
            "address_line1",
            "address_line2",
            "city",
            "region",
            "postal_code",
            "country",
            "time_zone",
            "currency",
            "house_type",
            "stay_policy",
            "str_registration_number",
            "business_licence_number",
            "business_licence_expiry",
            "house_rules",
            "notes",
        ],
        search=["name", "address_line1", "city"],
        filters=["house_type", "stay_policy"],
        related=[
            Related(_("Rooms, beds and spaces"), "spaces", "property"),
            Related(_("Leases with landlords"), "leases", "property"),
            Related(_("Tickets"), "tickets", "property"),
            Related(_("Cleaning roster"), "cleaning", "property"),
            Related(_("Correspondence"), "correspondence", "property"),
            Related(_("Incidents and warnings"), "incidents", "property"),
        ],
        attachment_kind=RecordFile.Kind.PHOTO,
    ),
    Resource(
        slug="spaces",
        model=Space,
        title=_("Spaces"),
        singular=_("Space"),
        icon="bed",
        columns=[
            (_("Name"), "name"),
            (_("Property"), "property"),
            (_("Type"), "kind"),
            (_("Inside"), "parent"),
            (_("Bathroom"), "bathroom"),
            (_("Monthly"), "monthly_rent"),
            (_("Nightly"), "nightly_rate"),
            (_("Status"), "status"),
        ],
        form_fields=[
            "property",
            "parent",
            "kind",
            "name",
            "floor",
            "bathroom",
            "furnished",
            "max_occupants",
            "monthly_rent",
            "nightly_rate",
            "deposit",
            "min_stay_nights",
            "status",
            "furniture",
            "description",
        ],
        search=["name", "property__name"],
        filters=["kind", "status"],
        status_field="status",
        related=[
            Related(_("Bookings and tenancies"), "tenancies", "space"),
            Related(_("Inspections"), "inspections", "space"),
            Related(_("Tickets"), "tickets", "space"),
            Related(_("Listings"), "listings", "space"),
        ],
        attachment_kind=RecordFile.Kind.PHOTO,
    ),
    Resource(
        slug="landlords",
        model=Landlord,
        title=_("Landlords"),
        singular=_("Landlord"),
        icon="key",
        columns=[(_("Name"), "name"), (_("Company"), "company"), (_("Phone"), "phone"), (_("Email"), "email")],
        form_fields=["name", "company", "email", "phone", "address", "payment_details", "notes"],
        search=["name", "company", "email", "phone"],
        related=[
            Related(_("Representatives"), "representatives", "landlord"),
            Related(_("Leases"), "leases", "landlord"),
            Related(_("Correspondence"), "correspondence", "landlord"),
        ],
        attachment_kind=RecordFile.Kind.AGREEMENT,
    ),
    Resource(
        slug="representatives",
        model=LandlordRepresentative,
        title=_("Landlord representatives"),
        singular=_("Representative"),
        nav=False,
        columns=[(_("Name"), "name"), (_("Landlord"), "landlord"), (_("Role"), "role"), (_("Phone"), "phone")],
        form_fields=["landlord", "name", "role", "email", "phone", "handles_repairs", "handles_inspections"],
        search=["name", "landlord__name"],
    ),
    Resource(
        slug="leases",
        model=Lease,
        title=_("Leases"),
        singular=_("Lease"),
        icon="file",
        columns=[
            (_("Property"), "property"),
            (_("Landlord"), "landlord"),
            (_("Ends"), "end_date"),
            (_("Days left"), _days_left),
            (_("Rent"), "monthly_rent"),
            (_("Currency"), "currency"),
            (_("Sublet consent"), "sublet_consent"),
            (_("Renewal"), "renewal_decision"),
        ],
        form_fields=[
            "property",
            "landlord",
            "representative",
            "start_date",
            "end_date",
            "monthly_rent",
            "currency",
            "deposit_paid",
            "rent_due_day",
            "notice_period_days",
            "sublet_consent",
            "renewal_decision",
            "renewal_follow_up",
            "notes",
        ],
        search=["property__name", "landlord__name"],
        filters=["renewal_decision"],
        related=[
            Related(_("Payments to landlord"), "payments", "lease"),
            Related(_("Correspondence"), "correspondence", "lease"),
            Related(_("Incidents"), "incidents", "lease"),
        ],
        attachment_kind=RecordFile.Kind.AGREEMENT,
    ),
    Resource(
        slug="people",
        model=Person,
        title=_("Tenants and contacts"),
        singular=_("Person"),
        icon="users",
        columns=[
            (_("Name"), lambda p: str(p)),
            (_("Stage"), "stage"),
            (_("Phone"), "phone"),
            (_("Email"), "email"),
            (_("Source"), "source"),
            (_("Type"), "kind"),
        ],
        form_fields=[
            "kind",
            "first_name",
            "last_name",
            "email",
            "phone",
            "preferred_language",
            "stage",
            "source",
            "occupation",
            "current_address",
            "emergency_contact",
            "references",
            "id_verified_on",
            "notes",
        ],
        search=["first_name", "last_name", "email", "phone"],
        filters=["stage", "kind"],
        status_field="stage",
        related=[
            Related(_("Tenancies and bookings"), "tenancies", "person"),
            Related(_("Tickets"), "tickets", "reported_by"),
            Related(_("Cleaning duties"), "cleaning", "person"),
            Related(_("Correspondence"), "correspondence", "person"),
        ],
        attachment_kind=RecordFile.Kind.ID_DOCUMENT,
    ),
    Resource(
        slug="tenancies",
        model=Tenancy,
        title=_("Tenancies and bookings"),
        singular=_("Tenancy"),
        icon="calendar",
        columns=[
            (_("Tenant"), "person"),
            (_("Space"), "space"),
            (_("Type"), "type"),
            (_("Check-in"), "start_date"),
            (_("Check-out"), "end_date"),
            (_("Rent"), "rent_amount"),
            (_("Per"), "rent_period"),
            (_("Channel"), "source_channel"),
            (_("Status"), "status"),
        ],
        form_fields=[
            "person",
            "space",
            "type",
            "status",
            "start_date",
            "end_date",
            "rent_amount",
            "rent_period",
            "currency",
            "deposit_amount",
            "source_channel",
            "external_reference",
            "notice_date",
            "actual_move_out",
            "deposit_refunded_on",
            "notes",
        ],
        search=["person__first_name", "person__last_name", "space__name", "space__property__name"],
        filters=["status", "type"],
        status_field="status",
        related=[
            Related(_("Payments"), "payments", "tenancy"),
            Related(_("Agreements"), "agreements", "tenancy"),
            Related(_("Deposit deductions"), "deductions", "tenancy"),
            Related(_("Inspections"), "inspections", "tenancy"),
            Related(_("Incidents and warnings"), "incidents", "tenancy"),
        ],
        attachment_kind=RecordFile.Kind.AGREEMENT,
    ),
    Resource(
        slug="payments",
        model=Payment,
        title=_("Payments"),
        singular=_("Payment"),
        icon="cash",
        columns=[
            (_("Date"), "paid_on"),
            (_("In/out"), "direction"),
            (_("Purpose"), "purpose"),
            (_("Amount"), "amount"),
            (_("Currency"), "currency"),
            (_("Tenancy"), "tenancy"),
            (_("Lease"), "lease"),
            (_("Status"), "status"),
        ],
        form_fields=[
            "direction",
            "purpose",
            "tenancy",
            "lease",
            "amount",
            "currency",
            "period",
            "paid_on",
            "method",
            "reference",
            "notes",
        ],
        search=["reference", "notes"],
        filters=["status", "direction", "purpose"],
        status_field="status",
        attachment_kind=RecordFile.Kind.PAYMENT_SLIP,
    ),
    Resource(
        slug="deductions",
        model=DepositDeduction,
        title=_("Deposit deductions"),
        singular=_("Deduction"),
        nav=False,
        columns=[(_("Tenancy"), "tenancy"), (_("Reason"), "reason"), (_("Amount"), "amount")],
        form_fields=["tenancy", "reason", "amount", "notes"],
        attachment_kind=RecordFile.Kind.PHOTO,
    ),
    Resource(
        slug="agreements",
        model=Agreement,
        title=_("Agreements"),
        singular=_("Agreement"),
        nav=False,
        columns=[(_("Tenancy"), "tenancy"), (_("Type"), "kind"), (_("Status"), "status"), (_("Signed"), "signed_at")],
        form_fields=["tenancy", "kind", "status", "sent_at", "signed_at"],
        status_field="status",
        attachment_kind=RecordFile.Kind.AGREEMENT,
    ),
    Resource(
        slug="tickets",
        model=Ticket,
        title=_("Tickets"),
        singular=_("Ticket"),
        icon="wrench",
        columns=[
            (_("Title"), "title"),
            (_("Property"), "property"),
            (_("Space"), "space"),
            (_("Priority"), "priority"),
            (_("Category"), "category"),
            (_("Status"), "status"),
            (_("Opened"), "created_at"),
        ],
        form_fields=[
            "property",
            "space",
            "reported_by",
            "category",
            "priority",
            "status",
            "title",
            "description",
            "landlord_responsibility",
            "landlord_notified_at",
            "assigned_to",
            "appointment_at",
            "cost",
            "completed_at",
        ],
        search=["title", "description", "property__name"],
        filters=["status", "priority", "category"],
        status_field="status",
        attachment_kind=RecordFile.Kind.PHOTO,
    ),
    Resource(
        slug="inspections",
        model=Inspection,
        title=_("Inspections"),
        singular=_("Inspection"),
        nav=False,
        columns=[(_("Space"), "space"), (_("Type"), "kind"), (_("Date"), "inspected_on"), (_("Tenancy"), "tenancy")],
        form_fields=["space", "tenancy", "kind", "inspected_on", "summary"],
        attachment_kind=RecordFile.Kind.PHOTO,
    ),
    Resource(
        slug="cleaning",
        model=CleaningDuty,
        title=_("Cleaning roster"),
        singular=_("Cleaning duty"),
        icon="sparkle",
        columns=[
            (_("Date"), "due_date"),
            (_("Type"), "kind"),
            (_("Property"), "property"),
            (_("Space"), "space"),
            (_("Person"), "person"),
            (_("Status"), "status"),
            (_("Charge"), "charge_amount"),
        ],
        form_fields=[
            "kind",
            "property",
            "space",
            "tenancy",
            "person",
            "due_date",
            "areas",
            "status",
            "charge_amount",
            "notes",
        ],
        filters=["kind", "status"],
        status_field="status",
        attachment_kind=RecordFile.Kind.PHOTO,
    ),
    Resource(
        slug="correspondence",
        model=Correspondence,
        title=_("Correspondence"),
        singular=_("Correspondence"),
        icon="mail",
        columns=[
            (_("Date"), "occurred_at"),
            (_("Subject"), "subject"),
            (_("With"), "counterpart"),
            (_("Name"), "counterpart_name"),
            (_("Channel"), "channel"),
            (_("Direction"), "direction"),
            (_("Status"), "status"),
            (_("Deadline"), "deadline"),
        ],
        form_fields=[
            "counterpart",
            "counterpart_name",
            "landlord",
            "lease",
            "property",
            "person",
            "channel",
            "direction",
            "occurred_at",
            "subject",
            "body",
            "deadline",
            "status",
        ],
        search=["subject", "body", "counterpart_name"],
        filters=["counterpart", "status", "channel"],
        status_field="status",
        attachment_kind=RecordFile.Kind.LETTER,
    ),
    Resource(
        slug="incidents",
        model=Incident,
        title=_("Incidents and warnings"),
        singular=_("Incident"),
        icon="alert",
        columns=[
            (_("Date"), "occurred_at"),
            (_("Type"), "kind"),
            (_("Issue"), "issue"),
            (_("Property"), "property"),
            (_("Tenancy"), "tenancy"),
            (_("Follow up"), "follow_up_on"),
        ],
        form_fields=[
            "kind",
            "property",
            "tenancy",
            "lease",
            "occurred_at",
            "issue",
            "description",
            "response",
            "outcome",
            "follow_up_on",
        ],
        search=["issue", "description"],
        filters=["kind"],
        attachment_kind=RecordFile.Kind.CHAT_SCREENSHOT,
    ),
]

RESOURCES += [
    Resource(
        slug="listings",
        model=Listing,
        title=_("Listings"),
        singular=_("Listing"),
        icon="megaphone",
        columns=[
            (_("Title"), "title"),
            (_("Space"), "space"),
            (_("Stay type"), "stay_type"),
            (_("Monthly"), lambda o: o.price_monthly()),
            (_("Nightly"), lambda o: o.price_nightly()),
            (_("Status"), "status"),
        ],
        form_fields=[
            "space",
            "title",
            "summary",
            "description",
            "amenities",
            "stay_type",
            "monthly_price",
            "nightly_price",
            "available_from",
            "contact_line",
            "status",
        ],
        search=["title", "summary", "description"],
        filters=["status", "stay_type"],
        related=[Related(_("Channels"), "listing-channels", "listing")],
        attachment_kind=RecordFile.Kind.PHOTO,
    ),
    Resource(
        slug="listing-channels",
        model=ListingChannel,
        title=_("Listing channels"),
        singular=_("Listing channel"),
        nav=False,
        columns=[
            (_("Channel"), "channel"),
            (_("Listing"), "listing"),
            (_("Group / target"), "target"),
            (_("Status"), "status"),
            (_("Posted"), "posted_at"),
        ],
        form_fields=["listing", "channel", "connection", "target", "external_id", "url", "status"],
        filters=["channel", "status"],
        attachment_kind=RecordFile.Kind.CHAT_SCREENSHOT,
    ),
    Resource(
        slug="connections",
        model=Connection,
        title=_("Connections"),
        singular=_("Connection"),
        icon="plug",
        columns=[
            (_("Name"), "name"),
            (_("Service"), "provider"),
            (_("On"), "is_enabled"),
            (_("Last success"), "last_success_at"),
            (_("Last error"), "last_error"),
        ],
        form_fields=["provider", "name", "account_id", "is_enabled"],
        detail_fields=["provider", "name", "account_id", "is_enabled", "last_success_at", "last_error"],
        form_class=ConnectionForm,
        admin_only=True,
    ),
]

BY_SLUG = {r.slug: r for r in RESOURCES}
NAV_GROUPS = [
    (_("Portfolio"), ["properties", "spaces", "landlords", "leases"]),
    (_("Tenants"), ["people", "tenancies", "payments"]),
    (_("Operations"), ["tickets", "cleaning", "correspondence", "incidents"]),
    (_("Marketing"), ["listings"]),
]
