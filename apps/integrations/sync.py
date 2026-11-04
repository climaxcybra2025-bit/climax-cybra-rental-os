"""Turn channel data into Rental OS records, and push Rental OS data out.

Webhooks are treated as a nudge only: for Hostaway, the reservation is fetched
again from the API before anything is written, so a forged webhook cannot
create bookings. Every import is logged in SyncLog and is idempotent.
"""

import logging
from datetime import UTC, date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.operations.models import Ticket
from apps.people.models import Person
from apps.records.models import Correspondence
from apps.tenancies.models import Tenancy

from .models import Connection, ListingChannel, SyncLog
from .postkit import build_post
from .providers.hostaway import CANCELLED_STATUSES, SKIP_STATUSES, HostawayClient, HostawayError, channel_slug
from .providers.telegram import TelegramClient, TelegramError

logger = logging.getLogger(__name__)


def _log(connection, direction, kind, status="ok", message="", key="", payload=None):
    return SyncLog.objects.create(
        workspace_id=connection.workspace_id,
        connection=connection,
        direction=direction,
        kind=kind,
        status=status,
        message=str(message)[:4000],
        idempotency_key=key,
        payload=payload or {},
    )


def _decimal(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return Decimal("0.00")


# --- Hostaway reservations ---------------------------------------------------------------------


def _space_for(connection, listing_map_id):
    lc = (
        ListingChannel.objects.active()
        .select_related("listing__space")
        .filter(connection=connection, external_id=str(listing_map_id))
        .first()
    )
    return lc.listing.space if lc else None


def _guest(connection, r):
    email = (r.get("guestEmail") or "").strip()
    first = (r.get("guestFirstName") or "").strip()
    last = (r.get("guestLastName") or "").strip()
    if not first and not last:
        first = (r.get("guestName") or "Guest").strip()
    people = Person.objects.active().filter(workspace_id=connection.workspace_id)
    person = people.filter(email__iexact=email).first() if email else None
    if person is None:
        person = Person.objects.create(
            workspace_id=connection.workspace_id,
            kind=Person.Kind.RENTER,
            first_name=first[:120] or "Guest",
            last_name=last[:120],
            email=email,
            phone=(r.get("phone") or "")[:32],
            source=channel_slug(r.get("channelName")),
            stage=Person.Stage.READY_FOR_CHECK_IN,
        )
    return person


def import_reservation(connection, r):
    """Create or update the Tenancy for one Hostaway reservation. Returns the tenancy or None."""
    rid = str(r.get("id") or "")
    if not rid:
        return None
    ref = f"hostaway:{rid}"
    status = r.get("status") or ""
    existing = Tenancy.objects.active().filter(workspace_id=connection.workspace_id, external_reference=ref).first()
    if status in SKIP_STATUSES and existing is None:
        _log(connection, "in", "reservation", "skipped", f"Status {status}", key=ref)
        return None
    space = _space_for(connection, r.get("listingMapId"))
    if space is None:
        _log(
            connection,
            "in",
            "reservation",
            "skipped",
            f"Hostaway listing {r.get('listingMapId')} is not linked to a Rental OS listing",
            key=ref,
        )
        return None
    start, end = parse_date(str(r.get("arrivalDate") or "")), parse_date(str(r.get("departureDate") or ""))
    if not start or not end:
        _log(connection, "in", "reservation", "error", "Missing arrival or departure date", key=ref, payload=r)
        return None
    tenancy = existing or Tenancy(workspace_id=connection.workspace_id, external_reference=ref)
    tenancy.space = space
    if not existing:
        tenancy.person = _guest(connection, r)
    tenancy.type = Tenancy.Type.SHORT_STAY
    tenancy.start_date, tenancy.end_date = start, end
    tenancy.rent_amount = _decimal(r.get("totalPrice"))
    tenancy.rent_period = "total"
    tenancy.currency = (r.get("currency") or space.get_currency() or "")[:3].upper()
    tenancy.source_channel = channel_slug(r.get("channelName"))
    if status in CANCELLED_STATUSES:
        tenancy.status = Tenancy.Status.CANCELLED
    elif tenancy.status in (None, "", Tenancy.Status.CANCELLED):
        tenancy.status = Tenancy.Status.RESERVED
    try:
        with transaction.atomic():
            tenancy.full_clean()
            tenancy.save()
    except (ValidationError, IntegrityError) as exc:
        _log(connection, "in", "reservation", "error", f"Conflicts with another booking: {exc}", key=ref, payload=r)
        Ticket.objects.create(
            workspace_id=connection.workspace_id,
            property=space.property,
            space=space,
            category=Ticket.Category.OTHER,
            priority=Ticket.Priority.URGENT,
            title=f"Channel booking overlaps another booking: {space.name} {start}–{end}",
            description=(
                f"{tenancy.source_channel} reservation {rid} for "
                f"{r.get('guestName') or r.get('guestFirstName', '')} could not be saved because the dates overlap "
                "another booking on this space. Block the dates on the channel or move one of the guests."
            ),
        )
        return None
    _log(connection, "in", "reservation", "ok", f"{tenancy.get_status_display()} {start}–{end}", key=ref)
    return tenancy


def import_message(connection, msg, tenancy=None):
    """Save one guest message as Correspondence (once). Host messages are saved as outgoing."""
    mid = str(msg.get("id") or "")
    if not mid:
        return None
    key = f"hostaway:msg:{mid}"
    qs = Correspondence.objects.filter(workspace_id=connection.workspace_id, external_message_id=key)
    if qs.exists():
        return qs.first()
    incoming = bool(int(msg.get("isIncoming") or 0))
    when = parse_datetime(str(msg.get("date") or msg.get("insertedOn") or "").replace(" ", "T")) or timezone.now()
    if timezone.is_naive(when):
        when = timezone.make_aware(when, UTC)
    if tenancy is None and msg.get("reservationId"):
        tenancy = (
            Tenancy.objects.active()
            .filter(workspace_id=connection.workspace_id, external_reference=f"hostaway:{msg['reservationId']}")
            .select_related("space__property", "person")
            .first()
        )
    body = (msg.get("body") or "").strip()
    c = Correspondence.objects.create(
        workspace_id=connection.workspace_id,
        counterpart=Correspondence.Counterpart.TENANT,
        person=tenancy.person if tenancy else None,
        property=tenancy.space.property if tenancy else None,
        counterpart_name=str(tenancy.person) if tenancy else "",
        channel=Correspondence.Channel.CHAT,
        direction=Correspondence.Direction.IN if incoming else Correspondence.Direction.OUT,
        occurred_at=when,
        subject=(body.splitlines()[0] if body else "Message")[:255],
        body=body,
        status=Correspondence.Status.NEEDS_REPLY if incoming else Correspondence.Status.NONE,
        external_message_id=key,
        external_thread_id=f"hostaway:{msg.get('conversationId') or ''}",
    )
    return c


def sync_conversations(connection, client, tenancy):
    rid = tenancy.external_reference.split(":", 1)[1]
    count = 0
    for conv in client.conversations(rid):
        for msg in client.messages(conv["id"]):
            msg.setdefault("conversationId", conv["id"])
            before = Correspondence.objects.filter(external_message_id=f"hostaway:msg:{msg.get('id')}").exists()
            if import_message(connection, msg, tenancy) and not before:
                count += 1
    return count


def sync_hostaway(connection, client=None, with_messages=True):
    """Pull recent reservations (and messages for current guests). Safe to run often."""
    client = client or HostawayClient(connection)
    imported = messages = 0
    try:
        for r in client.reservations():
            if import_reservation(connection, r):
                imported += 1
        if with_messages:
            today = date.today()
            current = Tenancy.objects.active().filter(
                workspace_id=connection.workspace_id,
                external_reference__startswith="hostaway:",
                status__in=["reserved", "active", "notice_given"],
                end_date__gte=today,
            )[:50]
            for t in current:
                messages += sync_conversations(connection, client, t)
    except HostawayError as exc:
        connection.mark_error(exc)
        _log(connection, "in", "sync", "error", exc)
        raise
    connection.mark_ok()
    return imported, messages


def handle_hostaway_webhook(connection, payload, client=None):
    """A webhook only tells us something changed; fetch the record from the API before saving."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    event = str(payload.get("event") or payload.get("object") or "")
    client = client or HostawayClient(connection)
    if "conversationId" in data or "message" in event.lower():
        reservation_id = data.get("reservationId")
        tenancy = None
        if reservation_id:
            tenancy = import_reservation(connection, client.reservation(reservation_id)) or (
                Tenancy.objects.active()
                .filter(workspace_id=connection.workspace_id, external_reference=f"hostaway:{reservation_id}")
                .first()
            )
        if tenancy:
            return "messages", sync_conversations(connection, client, tenancy)
        _log(connection, "in", "message", "skipped", "Message not tied to a known reservation", payload=data)
        return "messages", 0
    reservation_id = data.get("id") or data.get("reservationId")
    if not reservation_id:
        _log(connection, "in", "webhook", "skipped", "No reservation ID in webhook", payload=payload)
        return "ignored", 0
    tenancy = import_reservation(connection, client.reservation(reservation_id))
    return "reservation", 1 if tenancy else 0


# --- replies and posting -------------------------------------------------------------------------


class ChannelError(Exception):
    pass


def reply_via_channel(correspondence, text, user=None, client=None):
    """Send a reply into the guest's channel conversation and record it as outgoing correspondence."""
    thread = correspondence.external_thread_id or ""
    if not thread.startswith("hostaway:") or not thread.split(":", 1)[1]:
        raise ChannelError("This message did not come from a connected channel.")
    connection = (
        Connection.objects.active()
        .filter(workspace_id=correspondence.workspace_id, provider=Connection.Provider.HOSTAWAY, is_enabled=True)
        .first()
    )
    if connection is None:
        raise ChannelError("No active Hostaway connection.")
    client = client or HostawayClient(connection)
    try:
        result = client.send_message(thread.split(":", 1)[1], text)
    except HostawayError as exc:
        _log(connection, "out", "message", "error", exc)
        raise ChannelError(str(exc)) from exc
    mid = (result or {}).get("id")
    reply = Correspondence.objects.create(
        workspace_id=correspondence.workspace_id,
        counterpart=correspondence.counterpart,
        person=correspondence.person,
        property=correspondence.property,
        counterpart_name=correspondence.counterpart_name,
        channel=Correspondence.Channel.CHAT,
        direction=Correspondence.Direction.OUT,
        occurred_at=timezone.now(),
        subject=text.splitlines()[0][:255] if text else "Reply",
        body=text,
        external_message_id=f"hostaway:msg:{mid}" if mid else "",
        external_thread_id=thread,
    )
    correspondence.status = Correspondence.Status.REPLIED
    correspondence.save(update_fields=["status", "updated_at"])
    _log(connection, "out", "message", "ok", f"Reply to correspondence {correspondence.pk}")
    return reply


def post_listing_channel(lc, client=None):
    """Publish a listing to an automatic channel. Telegram posts; Hostaway channels are managed in Hostaway."""
    listing = lc.listing
    if lc.channel != ListingChannel.Channel.TELEGRAM:
        raise ChannelError(
            "Airbnb, Booking.com and Vrbo listings are created and priced in Hostaway; link them here by "
            "entering the Hostaway listing ID. Other channels are posted by hand with the post text."
        )
    connection = lc.connection
    if connection is None or connection.provider != Connection.Provider.TELEGRAM:
        raise ChannelError("Choose a Telegram connection for this channel first.")
    try:
        client = client or TelegramClient(connection)
        message_id = client.post(build_post(listing, lc.channel), [p.file for p in listing.photos()])
    except TelegramError as exc:
        lc.status, lc.last_error = ListingChannel.Status.ERROR, str(exc)
        lc.save(update_fields=["status", "last_error", "updated_at"])
        _log(connection, "out", "telegram.post", "error", exc)
        raise ChannelError(str(exc)) from exc
    lc.external_id = str(message_id)
    lc.url = client.message_url(message_id) or lc.url
    lc.status, lc.posted_at, lc.last_error = ListingChannel.Status.POSTED, timezone.now(), ""
    lc.save()
    connection.mark_ok()
    _log(connection, "out", "telegram.post", "ok", f"Listing {listing.pk} → message {message_id}")
    return lc
