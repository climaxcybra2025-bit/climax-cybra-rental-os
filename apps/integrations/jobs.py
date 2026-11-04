"""Scheduled work: daily jobs per workspace, channel syncs and event delivery.

Every job is safe to run twice: daily jobs record a JobRun per local date, and
turnover cleans are created once per tenancy.
"""

import logging
import zoneinfo
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.landlords.models import Lease
from apps.operations.models import CleaningDuty
from apps.properties.models import Space
from apps.tenancies.models import Tenancy
from apps.workspaces.models import Workspace

from . import events
from .models import Connection, JobRun
from .providers.hostaway import HostawayError
from .sync import sync_hostaway

logger = logging.getLogger(__name__)

LEASE_ALERT_DAYS = (90, 60, 30, 7)


def local_today(workspace):
    try:
        tz = zoneinfo.ZoneInfo(workspace.time_zone or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        tz = zoneinfo.ZoneInfo("UTC")
    return timezone.now().astimezone(tz)


def start_short_stays(workspace, day):
    """Short stays whose check-in is today become active."""
    qs = (
        Tenancy.objects.active()
        .filter(workspace=workspace, type=Tenancy.Type.SHORT_STAY, status=Tenancy.Status.RESERVED, start_date__lte=day)
        .exclude(end_date__lte=day)
    )
    count = 0
    for t in qs:
        t.status = Tenancy.Status.ACTIVE
        t.save(update_fields=["status", "updated_at"])
        count += 1
    return count


def checkouts(workspace, day):
    """Today's checkouts: schedule a turnover clean, flag the space, end short stays."""
    qs = (
        Tenancy.objects.active()
        .filter(workspace=workspace, end_date=day, status__in=["reserved", "active", "notice_given"])
        .select_related("space__property", "person")
    )
    created = 0
    for t in qs:
        events.emit(workspace, "tenancy.checkout_today", events.tenancy_payload(t))
        if not CleaningDuty.objects.active().filter(tenancy=t, kind=CleaningDuty.Kind.TURNOVER).exists():
            duty = CleaningDuty.objects.create(
                workspace=workspace,
                kind=CleaningDuty.Kind.TURNOVER,
                property=t.space.property,
                space=t.space,
                tenancy=t,
                due_date=day,
                areas="Full turnover: linens, bathroom, floors, restock, photos",
                notes=f"Checkout of {t.person} ({t.get_type_display()})",
            )
            events.emit(
                workspace,
                "cleaning.turnover_created",
                {"id": duty.pk, "space": t.space.name, "property": str(t.space.property), "due_date": day},
            )
            created += 1
        if t.space.status in (Space.Status.RENTED, Space.Status.AVAILABLE, Space.Status.ADVERTISED):
            t.space.status = Space.Status.TURNOVER
            t.space.save(update_fields=["status", "updated_at"])
        if t.type == Tenancy.Type.SHORT_STAY:
            t.status = Tenancy.Status.ENDED
            t.actual_move_out = t.actual_move_out or day
            t.save(update_fields=["status", "actual_move_out", "updated_at"])
    return created


def lease_alerts(workspace, day):
    """Leases ending in 90, 60, 30 or 7 days: one event each and one email digest."""
    due = []
    for n in LEASE_ALERT_DAYS:
        for lease in (
            Lease.objects.active()
            .filter(workspace=workspace, end_date=day + timedelta(days=n))
            .select_related("property", "landlord")
        ):
            due.append((n, lease))
            events.emit(
                workspace,
                "lease.expiring",
                {
                    "id": lease.pk,
                    "days_left": n,
                    "end_date": lease.end_date,
                    "property": str(lease.property),
                    "landlord": str(lease.landlord),
                    "renewal_decision": lease.renewal_decision,
                },
            )
    recipients = workspace.urgent_recipients()
    if due and recipients:
        lines = [f"- {lease.property}: ends {lease.end_date} ({n} days), landlord {lease.landlord}" for n, lease in due]
        send_mail(
            subject=f"Leases ending soon ({len(due)}) – {workspace.name}",
            message="These landlord leases are coming up for renewal:\n\n" + "\n".join(lines),
            from_email=None,
            recipient_list=recipients,
            fail_silently=True,
        )
    return len(due)


DAILY_JOBS = {
    "start_short_stays": start_short_stays,
    "checkouts": checkouts,
    "lease_alerts": lease_alerts,
}


def run_daily(workspace, day=None, force=False):
    """Run each daily job once for the workspace's local date. Returns {job: result}."""
    now_local = local_today(workspace)
    day = day or now_local.date()
    if not force and day == now_local.date() and now_local.hour < settings.DAILY_JOBS_HOUR:
        return {}
    results = {}
    for name, fn in DAILY_JOBS.items():
        try:
            with transaction.atomic():
                run = JobRun.objects.create(workspace=workspace, job=name, run_on=day)
                result = fn(workspace, day)
                run.result = {"count": result}
                run.save(update_fields=["result"])
            results[name] = result
        except IntegrityError:
            continue  # already ran today
        except Exception:  # one workspace's failure must not stop the others
            logger.exception("Daily job %s failed for workspace %s", name, workspace.pk)
    return results


def run_daily_all(day=None, force=False):
    return {ws.slug: run_daily(ws, day=day, force=force) for ws in Workspace.objects.filter(is_active=True)}


def sync_all_channels():
    results = {}
    for conn in Connection.objects.active().filter(provider=Connection.Provider.HOSTAWAY, is_enabled=True):
        try:
            results[conn.pk] = sync_hostaway(conn)
        except HostawayError as exc:
            results[conn.pk] = str(exc)
        except Exception:
            logger.exception("Hostaway sync failed for connection %s", conn.pk)
    return results
