import json
from datetime import date, timedelta

import pytest
import responses
from django.core import mail
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from apps.integrations import events, jobs
from apps.integrations.models import ApiKey, Connection, JobRun, Listing, ListingChannel, OutboundEvent, SyncLog
from apps.integrations.postkit import build_post
from apps.integrations.providers.hostaway import BASE_URL
from apps.integrations.sync import import_reservation, reply_via_channel
from apps.operations.models import CleaningDuty, Ticket
from apps.records.models import AuditEntry, Correspondence
from apps.tenancies.models import Tenancy

TG = "https://api.telegram.org/bot123:abc"


def client_for(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def owner(ws, make_user):
    return make_user("kimia", ws, "owner")


@pytest.fixture
def listing(ws, house):
    return Listing.objects.create(
        workspace=ws,
        space=house["couch"],
        title="Couch near Coquitlam Centre",
        stay_type="short",
        amenities="Wi-Fi\nLaundry",
        contact_line="WhatsApp +1 604 555 0100",
        status="ready",
    )


@pytest.fixture
def hostaway(ws, listing):
    conn = Connection.objects.create(workspace=ws, provider="hostaway", name="Hostaway", account_id="12345")
    conn.set_secret("hostaway-api-key")
    conn.save()
    ListingChannel.objects.create(workspace=ws, listing=listing, channel="airbnb", connection=conn, external_id="777")
    return conn


def reservation(**kw):
    r = {
        "id": 9001,
        "listingMapId": 777,
        "channelName": "airbnbOfficial",
        "guestFirstName": "Ana",
        "guestLastName": "Diaz",
        "guestEmail": "ana@example.com",
        "phone": "+34 600 000 000",
        "arrivalDate": str(date.today() + timedelta(days=3)),
        "departureDate": str(date.today() + timedelta(days=5)),
        "totalPrice": 120.5,
        "currency": "CAD",
        "status": "new",
    }
    r.update(kw)
    return r


def mock_token():
    responses.post(f"{BASE_URL}/accessTokens", json={"access_token": "tok"})


# --- listings and post text ---------------------------------------------------------------------


def test_post_text_has_price_but_no_street_address(listing):
    text = build_post(listing)
    assert "45" in text and "Coquitlam" in text and "Wi-Fi" in text
    assert "1682 Regan St" not in text


def test_listing_page_shows_post_text(ws, owner, listing):
    resp = client_for(owner).get(reverse("web:detail", args=["listings", listing.pk]))
    assert resp.status_code == 200 and b"Couch near Coquitlam Centre" in resp.content and b"post-text" in resp.content


def test_mark_hand_posted_channel(ws, owner, listing):
    lc = ListingChannel.objects.create(workspace=ws, listing=listing, channel="facebook_group", target="Van Rooms")
    resp = client_for(owner).post(
        reverse("web:listing-channel-posted", args=[lc.pk]), {"url": "https://facebook.com/groups/x/posts/1"}
    )
    assert resp.status_code == 302
    lc.refresh_from_db()
    assert lc.status == "posted" and lc.url.endswith("/1")


# --- connections ---------------------------------------------------------------------------------


def test_connection_secret_is_encrypted_and_never_shown(ws, owner):
    c = client_for(owner)
    resp = c.post(
        reverse("web:create", args=["connections"]),
        {"provider": "telegram", "name": "Bot", "account_id": "@rooms", "is_enabled": "on", "secret": "123:abc"},
    )
    assert resp.status_code == 302, resp.content
    conn = Connection.objects.get(name="Bot")
    assert conn.secret == "123:abc" and "123:abc" not in conn.secret_encrypted
    page = c.get(reverse("web:edit", args=["connections", conn.pk])).content.decode()
    assert "123:abc" not in page
    # Saving without a secret keeps the old one.
    c.post(
        reverse("web:edit", args=["connections", conn.pk]),
        {"provider": "telegram", "name": "Bot 2", "account_id": "@rooms", "is_enabled": "on"},
    )
    conn.refresh_from_db()
    assert conn.name == "Bot 2" and conn.secret == "123:abc"
    assert not any("secret_encrypted" in json.dumps(a.changes) for a in AuditEntry.objects.all())


def test_team_member_cannot_open_connections(ws, make_user):
    resp = client_for(make_user("helper", ws, "team")).get(reverse("web:list", args=["connections"]))
    assert resp.status_code == 302 and resp["Location"] == reverse("web:dashboard")


@responses.activate
def test_test_connection_telegram(ws, owner):
    conn = Connection.objects.create(workspace=ws, provider="telegram", name="Bot", account_id="@rooms")
    conn.set_secret("123:abc")
    conn.save()
    responses.post(f"{TG}/getMe", json={"ok": True, "result": {"username": "oceanway_bot"}})
    client_for(owner).post(reverse("web:connection-test", args=[conn.pk]))
    conn.refresh_from_db()
    assert conn.last_success_at and not conn.last_error


# --- Hostaway bookings and messages --------------------------------------------------------------


def test_import_reservation_creates_guest_and_booking_once(hostaway, house):
    t = import_reservation(hostaway, reservation())
    assert t.space == house["couch"] and t.status == "reserved" and t.source_channel == "airbnb"
    assert t.person.email == "ana@example.com" and str(t.rent_amount) == "120.50"
    again = import_reservation(hostaway, reservation(totalPrice=130))
    assert again.pk == t.pk and Tenancy.objects.count() == 1 and str(again.rent_amount) == "130.00"
    cancelled = import_reservation(hostaway, reservation(status="cancelled"))
    assert cancelled.status == "cancelled"


def test_unlinked_listing_is_skipped(hostaway):
    assert import_reservation(hostaway, reservation(listingMapId=1)) is None
    assert SyncLog.objects.filter(status="skipped").exists()


def test_overlapping_channel_booking_raises_urgent_ticket(
    hostaway, house, tenant_person, ws, django_capture_on_commit_callbacks
):
    Tenancy.objects.create(
        workspace=ws,
        space=house["couch"],
        person=tenant_person,
        type="short_stay",
        status="reserved",
        start_date=date.today() + timedelta(days=4),
        end_date=date.today() + timedelta(days=6),
        rent_amount=90,
        rent_period="total",
        currency="CAD",
    )
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert import_reservation(hostaway, reservation()) is None
    ticket = Ticket.objects.get()
    assert ticket.priority == "urgent" and "overlaps" in ticket.title
    assert len(mail.outbox) == 1


@responses.activate
def test_webhook_refetches_reservation(hostaway, client):
    mock_token()
    responses.get(f"{BASE_URL}/reservations/9001", json={"status": "success", "result": reservation()})
    resp = client.post(
        reverse("hooks:hostaway", args=[hostaway.webhook_token]),
        data=json.dumps({"event": "reservation.created", "data": {"id": 9001, "guestEmail": "forged@x.com"}}),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    assert Tenancy.objects.get().person.email == "ana@example.com"  # API data, not the webhook body


def test_webhook_wrong_token_is_404(hostaway, client):
    resp = client.post("/hooks/hostaway/nope/", data="{}", content_type="application/json")
    assert resp.status_code == 404


@responses.activate
def test_message_webhook_saves_guest_message(hostaway, client):
    mock_token()
    responses.get(f"{BASE_URL}/reservations/9001", json={"status": "success", "result": reservation()})
    responses.get(f"{BASE_URL}/conversations", json={"status": "success", "result": [{"id": 55}]})
    responses.get(
        f"{BASE_URL}/conversations/55/messages",
        json={
            "status": "success",
            "result": [
                {"id": 1, "body": "What is the Wi-Fi password?", "isIncoming": 1, "date": "2026-11-04 10:00:00"},
            ],
        },
    )
    payload = {"event": "message.received", "data": {"conversationId": 55, "reservationId": 9001}}
    url = reverse("hooks:hostaway", args=[hostaway.webhook_token])
    for _ in range(2):  # duplicates are ignored
        assert client.post(url, data=json.dumps(payload), content_type="application/json").status_code == 200
    c = Correspondence.objects.get()
    assert c.status == "needs_reply" and c.person.email == "ana@example.com" and c.external_thread_id == "hostaway:55"


@responses.activate
def test_reply_goes_through_hostaway(hostaway, owner):
    c = Correspondence.objects.create(
        workspace=hostaway.workspace,
        counterpart="tenant",
        channel="chat",
        direction="in",
        occurred_at="2026-11-04T10:00Z",
        subject="Wi-Fi?",
        external_thread_id="hostaway:55",
    )
    mock_token()
    responses.post(f"{BASE_URL}/conversations/55/messages", json={"status": "success", "result": {"id": 2}})
    resp = client_for(owner).post(reverse("web:correspondence-reply", args=[c.pk]), {"body": "It is oceanway2026"})
    assert resp.status_code == 302
    c.refresh_from_db()
    assert c.status == "replied"
    out = Correspondence.objects.get(direction="out")
    assert out.body == "It is oceanway2026"
    assert json.loads(responses.calls[-1].request.body)["body"] == "It is oceanway2026"


def test_reply_without_channel_is_refused(ws, owner):
    c = Correspondence.objects.create(
        workspace=ws,
        counterpart="landlord",
        channel="email",
        direction="in",
        occurred_at="2026-11-04T10:00Z",
        subject="Hi",
    )
    from apps.integrations.sync import ChannelError

    with pytest.raises(ChannelError):
        reply_via_channel(c, "hello")


@responses.activate
def test_sync_command_pulls_reservations(hostaway):
    mock_token()
    responses.get(f"{BASE_URL}/reservations", json={"status": "success", "result": [reservation()]})
    responses.get(f"{BASE_URL}/conversations", json={"status": "success", "result": []})
    call_command("sync_channels")
    assert Tenancy.objects.count() == 1
    hostaway.refresh_from_db()
    assert hostaway.last_success_at


# --- Telegram ------------------------------------------------------------------------------------


@responses.activate
def test_post_listing_to_telegram(ws, owner, listing):
    conn = Connection.objects.create(workspace=ws, provider="telegram", name="Bot", account_id="@oceanway_rooms")
    conn.set_secret("123:abc")
    conn.save()
    lc = ListingChannel.objects.create(workspace=ws, listing=listing, channel="telegram", connection=conn)
    responses.post(f"{TG}/sendMessage", json={"ok": True, "result": {"message_id": 42}})
    client_for(owner).post(reverse("web:listing-channel-post", args=[lc.pk]))
    lc.refresh_from_db()
    assert lc.status == "posted" and lc.url == "https://t.me/oceanway_rooms/42"


@responses.activate
def test_telegram_error_is_recorded(ws, owner, listing):
    conn = Connection.objects.create(workspace=ws, provider="telegram", name="Bot", account_id="@rooms")
    conn.set_secret("123:abc")
    conn.save()
    lc = ListingChannel.objects.create(workspace=ws, listing=listing, channel="telegram", connection=conn)
    responses.post(f"{TG}/sendMessage", json={"ok": False, "description": "Forbidden: bot is not a member"})
    client_for(owner).post(reverse("web:listing-channel-post", args=[lc.pk]))
    lc.refresh_from_db()
    assert lc.status == "error" and "not a member" in lc.last_error


# --- n8n events ----------------------------------------------------------------------------------


@pytest.fixture
def n8n(ws):
    conn = Connection.objects.create(
        workspace=ws, provider="n8n", name="n8n", account_id="https://n8n.example.com/webhook/rental"
    )
    conn.set_secret("signing-secret")
    conn.save()
    return conn


@responses.activate
def test_ticket_event_is_signed_and_delivered(ws, house, n8n, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        Ticket.objects.create(
            workspace=ws, property=house["property"], category="plumbing", priority="urgent", title="Pipe burst"
        )
    event = OutboundEvent.objects.get(event="ticket.created")
    responses.post(n8n.account_id, json={"ok": True})
    assert events.deliver_due() == (1, 0)
    req = responses.calls[0].request
    assert req.headers["X-RentalOS-Signature"] == events.sign("signing-secret", req.body)
    assert json.loads(req.body)["data"]["priority"] == "urgent"
    event.refresh_from_db()
    assert event.delivered_at


@responses.activate
def test_failed_delivery_is_retried_later(ws, n8n):
    events.emit(ws, "connection.test", {})
    responses.post(n8n.account_id, status=500)
    assert events.deliver_due() == (0, 1)
    e = OutboundEvent.objects.get()
    assert e.attempts == 1 and e.next_attempt_at > e.created_at and not e.failed_at
    assert events.deliver_due() == (0, 0)  # not due yet


def test_no_events_without_n8n(ws, house):
    assert events.emit(ws, "ticket.created", {}) == 0


# --- API keys ------------------------------------------------------------------------------------


def test_api_key_reads_only_its_workspace(ws, owner, house, make_workspace, make_user):
    other = make_workspace("other")
    from apps.properties.models import Property

    Property.objects.create(
        workspace=other,
        name="Elsewhere",
        address_line1="x",
        city="Tokyo",
        country="JP",
        time_zone="Asia/Tokyo",
        currency="JPY",
    )
    make_user("someone", other, "owner")
    _key, raw = ApiKey.issue(ws, "n8n", owner)
    api = APIClient()
    resp = api.get("/api/properties/", HTTP_AUTHORIZATION=f"Api-Key {raw}")
    assert resp.status_code == 200
    assert [p["name"] for p in resp.json()["results"]] == ["Regan St"]


def test_revoked_or_wrong_key_is_rejected(ws, owner):
    key, raw = ApiKey.issue(ws, "n8n", owner)
    api = APIClient()
    assert api.get("/api/properties/", HTTP_AUTHORIZATION="Api-Key ros_wrong").status_code in (401, 403)
    key.revoked_at = key.created_at
    key.save()
    assert api.get("/api/properties/", HTTP_AUTHORIZATION=f"Api-Key {raw}").status_code in (401, 403)


def test_api_keys_page_shows_new_key_once(ws, owner):
    c = client_for(owner)
    resp = c.post(reverse("web:api-keys"), {"name": "n8n"})
    assert resp.status_code == 200 and b"ros_" in resp.content
    key = ApiKey.objects.get()
    assert key.key_hash not in resp.content.decode()
    assert b"ros_" not in c.get(reverse("web:api-keys")).content.replace(key.prefix.encode(), b"")


# --- daily jobs ----------------------------------------------------------------------------------


def test_checkout_creates_turnover_clean_once(ws, house, tenant_person):
    today = date.today()
    t = Tenancy.objects.create(
        workspace=ws,
        space=house["couch"],
        person=tenant_person,
        type="short_stay",
        status="active",
        start_date=today - timedelta(days=2),
        end_date=today,
        rent_amount=90,
        rent_period="total",
        currency="CAD",
    )
    jobs.run_daily(ws, day=today, force=True)
    jobs.run_daily(ws, day=today, force=True)
    duty = CleaningDuty.objects.get()
    assert duty.kind == "turnover" and duty.space == house["couch"] and duty.tenancy == t
    t.refresh_from_db()
    house["couch"].refresh_from_db()
    assert t.status == "ended" and house["couch"].status == "turnover"
    assert JobRun.objects.filter(job="checkouts").count() == 1


def test_short_stay_becomes_active_on_check_in(ws, house, tenant_person):
    today = date.today()
    t = Tenancy.objects.create(
        workspace=ws,
        space=house["couch"],
        person=tenant_person,
        type="short_stay",
        status="reserved",
        start_date=today,
        end_date=today + timedelta(days=2),
        rent_amount=90,
        rent_period="total",
        currency="CAD",
    )
    jobs.run_daily(ws, day=today, force=True)
    t.refresh_from_db()
    assert t.status == "active"


def test_lease_alert_emails_at_30_days(ws, lease):
    mail.outbox.clear()
    jobs.run_daily(ws, day=lease.end_date - timedelta(days=30), force=True)
    assert len(mail.outbox) == 1 and "Regan St" in mail.outbox[0].body


def test_scheduler_once(ws):
    call_command("run_scheduler", "--once")
