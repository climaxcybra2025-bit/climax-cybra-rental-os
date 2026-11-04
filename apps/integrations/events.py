"""Business events sent to n8n.

``emit()`` writes one OutboundEvent per enabled n8n connection in the
workspace; ``deliver_due()`` posts them with an HMAC signature and retries
failures with exponential backoff. Signals below emit the standard events.
"""

import hashlib
import hmac
import logging
from datetime import timedelta

import requests
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from apps.operations.models import Ticket
from apps.records.models import Correspondence
from apps.tenancies.models import Payment, Tenancy

from .models import Connection, OutboundEvent

logger = logging.getLogger(__name__)

EVENTS = {
    "ticket.created": "A ticket was opened (check data.priority for urgent)",
    "payment.submitted": "A payment is waiting for staff confirmation",
    "payment.confirmed": "Staff confirmed a payment",
    "tenancy.created": "A booking or tenancy was created (any channel)",
    "tenancy.status_changed": "A tenancy moved to another status",
    "tenancy.checkout_today": "A guest or tenant checks out today (daily job)",
    "lease.expiring": "A landlord lease ends in 90, 60, 30 or 7 days (daily job)",
    "correspondence.received": "A message or letter came in",
    "cleaning.turnover_created": "A turnover clean was scheduled after a checkout",
}


def emit(workspace, event, payload):
    """Queue an event for every enabled n8n connection of the workspace. Returns the number queued."""
    connections = Connection.objects.active().filter(
        workspace=workspace, provider=Connection.Provider.N8N, is_enabled=True
    )
    created = [OutboundEvent(workspace=workspace, connection=c, event=event, payload=payload) for c in connections]
    OutboundEvent.objects.bulk_create(created)
    return len(created)


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver(event, session=None):
    http = session or requests
    conn = event.connection
    body = event.body()
    headers = {"Content-Type": "application/json", "X-RentalOS-Event": event.event}
    if conn.has_secret:
        headers["X-RentalOS-Signature"] = sign(conn.secret, body)
    event.attempts += 1
    try:
        resp = http.post(conn.account_id, data=body, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        event.last_error = str(exc)[:2000]
        if event.attempts >= OutboundEvent.MAX_ATTEMPTS:
            event.failed_at = timezone.now()
        else:
            event.next_attempt_at = timezone.now() + timedelta(minutes=2**event.attempts)
        event.save()
        conn.mark_error(f"n8n delivery failed: {exc}")
        return False
    event.delivered_at = timezone.now()
    event.last_error = ""
    event.save()
    return True


def deliver_due(limit=200, session=None):
    due = (
        OutboundEvent.objects.select_related("connection", "workspace")
        .filter(delivered_at__isnull=True, failed_at__isnull=True, next_attempt_at__lte=timezone.now())
        .filter(connection__is_enabled=True, connection__archived_at__isnull=True)[:limit]
    )
    sent = failed = 0
    for event in due:
        if deliver(event, session=session):
            sent += 1
        else:
            failed += 1
    return sent, failed


# --- payloads -----------------------------------------------------------------------------------


def ticket_payload(t):
    return {
        "id": t.pk,
        "title": t.title,
        "priority": t.priority,
        "category": t.category,
        "status": t.status,
        "property": str(t.property),
        "space": t.space.name if t.space_id else None,
        "reported_by": str(t.reported_by) if t.reported_by_id else None,
        "description": t.description,
    }


def tenancy_payload(t):
    return {
        "id": t.pk,
        "status": t.status,
        "type": t.type,
        "person": {"id": t.person_id, "name": str(t.person), "email": t.person.email, "phone": t.person.phone},
        "space": {"id": t.space_id, "name": t.space.name, "property": str(t.space.property)},
        "start_date": t.start_date,
        "end_date": t.end_date,
        "rent_amount": t.rent_amount,
        "rent_period": t.rent_period,
        "currency": t.currency,
        "source_channel": t.source_channel,
    }


def payment_payload(p):
    return {
        "id": p.pk,
        "direction": p.direction,
        "purpose": p.purpose,
        "amount": p.amount,
        "currency": p.currency,
        "status": p.status,
        "tenancy_id": p.tenancy_id,
        "lease_id": p.lease_id,
    }


def _queue(instance, event, payload_fn):
    transaction.on_commit(lambda: emit(instance.workspace, event, payload_fn(instance)))


@receiver(post_save, sender=Ticket)
def _ticket_saved(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        _queue(instance, "ticket.created", ticket_payload)


@receiver(pre_save, sender=Tenancy)
@receiver(pre_save, sender=Payment)
def _remember_status(sender, instance, raw=False, **kwargs):
    instance._previous_status = (
        sender.objects.filter(pk=instance.pk).values_list("status", flat=True).first() if instance.pk else None
    )


@receiver(post_save, sender=Tenancy)
def _tenancy_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    if created:
        _queue(instance, "tenancy.created", tenancy_payload)
    elif getattr(instance, "_previous_status", None) not in (None, instance.status):
        _queue(instance, "tenancy.status_changed", tenancy_payload)


@receiver(post_save, sender=Payment)
def _payment_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    if instance.status == Payment.Status.PENDING and created:
        _queue(instance, "payment.submitted", payment_payload)
    elif instance.status == Payment.Status.CONFIRMED and getattr(instance, "_previous_status", None) != "confirmed":
        _queue(instance, "payment.confirmed", payment_payload)


@receiver(post_save, sender=Correspondence)
def _correspondence_saved(sender, instance, created, raw=False, **kwargs):
    if created and not raw and instance.direction == Correspondence.Direction.IN:
        _queue(
            instance,
            "correspondence.received",
            lambda c: {
                "id": c.pk,
                "counterpart": c.counterpart,
                "channel": c.channel,
                "subject": c.subject,
                "body": c.body,
                "person_id": c.person_id,
                "status": c.status,
            },
        )
