from datetime import date, datetime
from decimal import Decimal

from django import template
from django.db import models
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.html import format_html
from django.utils.translation import gettext as _

register = template.Library()

STATUS_TONES = {
    "available": "good",
    "advertised": "info",
    "on_hold": "warn",
    "rented": "neutral",
    "turnover": "warn",
    "blocked": "bad",
    "reserved": "info",
    "active": "good",
    "notice_given": "warn",
    "ended": "neutral",
    "cancelled": "neutral",
    "pending": "warn",
    "confirmed": "good",
    "rejected": "bad",
    "open": "warn",
    "scheduled": "info",
    "in_progress": "info",
    "completed": "good",
    "closed": "neutral",
    "urgent": "bad",
    "normal": "neutral",
    "missed": "bad",
    "proof_received": "good",
    "needs_reply": "warn",
    "replied": "good",
    "waiting": "info",
    "signed": "good",
    "draft": "neutral",
    "sent": "info",
    "warning": "warn",
    "incident": "bad",
    "reminder": "info",
}

LINKABLE = {
    "Property": "properties",
    "Space": "spaces",
    "Landlord": "landlords",
    "LandlordRepresentative": "representatives",
    "Lease": "leases",
    "Person": "people",
    "Tenancy": "tenancies",
    "Payment": "payments",
    "Ticket": "tickets",
}


def _resolve(obj, accessor):
    if callable(accessor):
        return accessor(obj), None
    value = obj
    field = None
    for part in accessor.split("."):
        if value is None:
            return None, None
        try:
            field = value._meta.get_field(part)
        except Exception:
            field = None
        if field is not None and getattr(field, "choices", None):
            display = getattr(value, f"get_{part}_display")()
            raw = getattr(value, part)
            return (display, raw), field
        value = getattr(value, part)
    return value, field


@register.simple_tag
def cell(obj, accessor, status_field=""):
    value, field = _resolve(obj, accessor)
    if isinstance(value, tuple):
        display, raw = value
        if not raw:
            return "—"
        tone = STATUS_TONES.get(raw)
        if tone:
            return format_html('<span class="badge badge-{}">{}</span>', tone, display)
        return display
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return format_html(
            '<span class="badge badge-{}">{}</span>', "good" if value else "neutral", _("Yes") if value else _("No")
        )
    if isinstance(value, datetime):
        return formats.date_format(timezone.localtime(value), "SHORT_DATETIME_FORMAT")
    if isinstance(value, date):
        return formats.date_format(value, "SHORT_DATE_FORMAT")
    if isinstance(value, Decimal):
        return formats.number_format(value, 2, use_l10n=True, force_grouping=True)
    if isinstance(value, models.Model):
        slug = LINKABLE.get(type(value).__name__)
        if slug:
            return format_html('<a href="{}">{}</a>', reverse("web:detail", args=[slug, value.pk]), value)
        return str(value)
    return value


@register.simple_tag
def detail_value(obj, name):
    return cell(obj, name)


@register.filter
def tone(value):
    return STATUS_TONES.get(value, "neutral")


@register.simple_tag(takes_context=True)
def query_with(context, **kwargs):
    params = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v in (None, ""):
            params.pop(k, None)
        else:
            params[k] = v
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


@register.simple_tag(takes_context=True)
def toggle_param(context, name, value, current):
    """Link that sets ``name=value``, or clears it when it is already the current value."""
    params = context["request"].GET.copy()
    params.pop("page", None)
    if current == value:
        params.pop(name, None)
    else:
        params[name] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"
