from datetime import date, timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.operations.models import Ticket
from apps.properties.models import Property
from apps.records.models import AuditEntry
from apps.tenancies.models import Payment, Tenancy


def make_tenancy(ws, space, person, start, end, **kw):
    return Tenancy(
        workspace=ws,
        space=space,
        person=person,
        type="fixed_term",
        start_date=start,
        end_date=end,
        rent_amount=1000,
        rent_period="month",
        currency="CAD",
        **kw,
    )


def test_workspaces_are_isolated(make_workspace, make_user, house):
    other = make_workspace("otherco", country="US", base_currency="USD")
    Property.objects.create(
        workspace=other,
        name="Austin house",
        address_line1="1 Main",
        city="Austin",
        country="US",
        time_zone="America/Chicago",
        currency="USD",
    )
    client = APIClient()
    client.force_login(make_user("rival", other, "owner"))
    names = [p["name"] for p in client.get("/api/properties/").json()["results"]]
    assert names == ["Austin house"]


def test_cannot_link_record_from_another_workspace(make_workspace, make_user, house):
    other = make_workspace("otherco")
    client = APIClient()
    client.force_login(make_user("rival", other, "owner"))
    resp = client.post("/api/spaces/", {"property": house["property"].pk, "kind": "room", "name": "Sneaky"})
    assert resp.status_code == 400


def test_delete_archives_instead_of_removing(staff_client, house):
    couch = house["couch"]
    assert staff_client.delete(f"/api/spaces/{couch.pk}/").status_code == 204
    couch.refresh_from_db()
    assert couch.archived_at is not None
    assert AuditEntry.objects.filter(object_id=couch.pk, action="archive").exists()


def test_database_blocks_double_booking_same_space(ws, house, tenant_person):
    make_tenancy(ws, house["couch"], tenant_person, date(2026, 12, 1), date(2026, 12, 3), status="active").save()
    with pytest.raises(IntegrityError), transaction.atomic():
        make_tenancy(ws, house["couch"], tenant_person, date(2026, 12, 2), date(2026, 12, 4), status="active").save()


def test_back_to_back_stays_are_allowed(ws, house, tenant_person):
    make_tenancy(ws, house["couch"], tenant_person, date(2026, 12, 1), date(2026, 12, 2), status="active").save()
    make_tenancy(ws, house["couch"], tenant_person, date(2026, 12, 2), date(2026, 12, 3), status="active").save()


def test_bed_overlapping_room_booking_is_rejected(ws, house, tenant_person):
    make_tenancy(ws, house["room"], tenant_person, date(2026, 8, 1), date(2026, 10, 30), status="active").save()
    clash = make_tenancy(ws, house["bed"], tenant_person, date(2026, 9, 1), date(2026, 9, 5), status="reserved")
    with pytest.raises(ValidationError):
        clash.full_clean()


def test_payment_must_link_to_tenancy_or_lease(ws):
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(
            workspace=ws, direction="in", purpose="rent", amount=100, currency="CAD", paid_on=date.today()
        )


def test_urgent_ticket_emails_admin_and_partner(ws, house, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        Ticket.objects.create(
            workspace=ws,
            property=house["property"],
            category="plumbing",
            priority="urgent",
            title="Pipe burst in kitchen",
        )
    assert len(mail.outbox) == 1
    assert set(mail.outbox[0].to) == {"admin@example.com", "partner@example.com"}
    assert "URGENT" in mail.outbox[0].subject


def test_normal_ticket_sends_no_email(ws, house, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        Ticket.objects.create(workspace=ws, property=house["property"], category="internet", title="Slow Wi-Fi")
    assert mail.outbox == []


def test_tenant_portal_ticket(ws, house, tenant_person, make_user, django_capture_on_commit_callbacks):
    user = make_user("sam", ws, "tenant")
    tenant_person.user = user
    tenant_person.save()
    make_tenancy(
        ws,
        house["room"],
        tenant_person,
        date.today() - timedelta(days=10),
        date.today() + timedelta(days=80),
        status="active",
    ).save()
    client = APIClient()
    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        resp = client.post(
            "/api/portal/tickets/", {"category": "plumbing", "priority": "urgent", "title": "Toilet overflowing"}
        )
    assert resp.status_code == 201, resp.content
    ticket = Ticket.objects.get(pk=resp.json()["id"])
    assert ticket.property == house["property"] and ticket.reported_by == tenant_person
    assert len(mail.outbox) == 1
    listing = client.get("/api/portal/tickets/").json()
    assert listing["emergency_phone"] == "+1 604 555 0100"
    # Tenants cannot reach the staff API.
    assert client.get("/api/properties/").status_code == 403


def test_dashboard_counts(staff_client, ws, lease, house):
    lease.end_date = date.today() + timedelta(days=45)
    lease.save()
    data = staff_client.get("/api/dashboard/").json()
    assert data["spaces_total"] == 3
    assert data["spaces_by_kind"] == {"room": 1, "bed": 1, "couch": 1}
    assert data["leases_ending"] == {"within_30_days": 0, "within_60_days": 1, "within_90_days": 1}
    assert data["leases_ending_soon"][0]["days_left"] == 45
