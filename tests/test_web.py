from datetime import date, timedelta

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.integrations.models import Connection, Listing, ListingChannel
from apps.landlords.models import LandlordRepresentative
from apps.operations.models import CleaningDuty, Inspection, Ticket
from apps.records.models import Correspondence, Incident, RecordFile
from apps.tenancies.models import Agreement, DepositDeduction, Payment, Tenancy
from apps.web.resources import RESOURCES


@pytest.fixture
def staff(ws, make_user):
    c = Client()
    c.force_login(make_user("kimia", ws, "owner"))
    return c


@pytest.fixture
def full_data(ws, house, lease, tenant_person):
    t = Tenancy.objects.create(
        workspace=ws,
        space=house["room"],
        person=tenant_person,
        type="fixed_term",
        status="active",
        start_date=date.today() - timedelta(days=5),
        end_date=date.today() + timedelta(days=85),
        rent_amount=1000,
        rent_period="month",
        currency="CAD",
        deposit_amount=450,
    )
    rep = LandlordRepresentative.objects.create(workspace=ws, landlord=lease.landlord, name="Rep")
    p = Payment.objects.create(
        workspace=ws, direction="in", purpose="rent", tenancy=t, amount=1000, currency="CAD", paid_on=date.today()
    )
    Payment.objects.create(
        workspace=ws,
        direction="out",
        purpose="landlord_rent",
        lease=lease,
        amount=3200,
        currency="CAD",
        paid_on=date.today(),
    )
    tk = Ticket.objects.create(
        workspace=ws, property=house["property"], space=house["room"], category="plumbing", title="Leak"
    )
    objs = {
        "properties": house["property"],
        "spaces": house["room"],
        "landlords": lease.landlord,
        "representatives": rep,
        "leases": lease,
        "people": tenant_person,
        "tenancies": t,
        "payments": p,
        "tickets": tk,
        "deductions": DepositDeduction.objects.create(workspace=ws, tenancy=t, reason="Carpet", amount=50),
        "agreements": Agreement.objects.create(workspace=ws, tenancy=t, kind="rental"),
        "inspections": Inspection.objects.create(
            workspace=ws, space=house["room"], kind="move_in", inspected_on=date.today()
        ),
        "cleaning": CleaningDuty.objects.create(
            workspace=ws, property=house["property"], person=tenant_person, due_date=date.today()
        ),
        "correspondence": Correspondence.objects.create(
            workspace=ws,
            counterpart="landlord",
            landlord=lease.landlord,
            channel="email",
            direction="in",
            occurred_at="2026-11-01T10:00Z",
            subject="Rent increase",
        ),
        "incidents": Incident.objects.create(
            workspace=ws,
            kind="warning",
            property=house["property"],
            tenancy=t,
            occurred_at="2026-11-01T10:00Z",
            issue="Noise after 11 PM",
        ),
    }
    listing = Listing.objects.create(workspace=ws, space=house["room"], title="Sunny room near SkyTrain")
    conn = Connection.objects.create(workspace=ws, provider="telegram", name="Rooms channel", account_id="@rooms")
    objs["listings"] = listing
    objs["listing-channels"] = ListingChannel.objects.create(
        workspace=ws, listing=listing, channel="facebook_group", target="Vancouver Rooms"
    )
    objs["connections"] = conn
    return objs


def test_login_page_and_redirects(staff, client):
    assert client.get("/").status_code == 302
    assert client.get(reverse("login")).status_code == 200
    resp = staff.get("/")
    assert resp.status_code == 302 and resp["Location"] == reverse("web:dashboard")


def test_login_with_email(ws, make_user, client):
    user = make_user("kimia", ws, "owner")
    user.email = "kimia@example.com"
    user.save()
    resp = client.post(reverse("login"), {"username": "kimia@example.com", "password": "pass-12345678"})
    assert resp.status_code == 302


def test_dashboard_renders(staff, full_data):
    resp = staff.get(reverse("web:dashboard"))
    assert resp.status_code == 200
    assert b"Leak" in resp.content


@pytest.mark.parametrize("res", [r.slug for r in RESOURCES])
def test_every_screen_renders(staff, full_data, res):
    obj = full_data[res]
    for url in [
        reverse("web:list", args=[res]),
        reverse("web:create", args=[res]),
        reverse("web:detail", args=[res, obj.pk]),
        reverse("web:edit", args=[res, obj.pk]),
    ]:
        resp = staff.get(url)
        assert resp.status_code == 200, (url, resp.status_code)


def test_persian_is_right_to_left(staff, full_data):
    staff.cookies["django_language"] = "fa"
    resp = staff.get(reverse("web:list", args=["properties"]))
    assert b'dir="rtl"' in resp.content


def test_create_payment_with_slip(staff, ws, full_data):
    t = full_data["tenancies"]
    slip = SimpleUploadedFile("etransfer.png", b"\x89PNG fake", content_type="image/png")
    resp = staff.post(
        reverse("web:create", args=["payments"]),
        {
            "direction": "in",
            "purpose": "deposit",
            "tenancy": t.pk,
            "amount": "450.00",
            "currency": "CAD",
            "paid_on": "2026-11-01",
            "method": "e-transfer",
            "attachments": [slip],
            "attachment_kind": "payment_slip",
        },
    )
    assert resp.status_code == 302, resp.content
    payment = Payment.objects.get(purpose="deposit")
    files = RecordFile.objects.filter(object_id=payment.pk, content_type__model="payment")
    assert files.count() == 1 and files.first().kind == "payment_slip"
    detail = staff.get(resp["Location"])
    assert b"etransfer.png" in detail.content
    file_resp = staff.get(reverse("web:file", args=[files.first().pk]))
    assert file_resp.status_code == 200


def test_cannot_open_other_workspace_records(make_workspace, make_user, full_data):
    other = make_workspace("otherco")
    c = Client()
    c.force_login(make_user("rival", other, "owner"))
    assert c.get(reverse("web:detail", args=["properties", full_data["properties"].pk])).status_code == 404
    form = c.get(reverse("web:create", args=["spaces"]))
    assert full_data["properties"].name.encode() not in form.content


def test_form_rejects_double_booking(staff, full_data, tenant_person):
    resp = staff.post(
        reverse("web:create", args=["tenancies"]),
        {
            "person": tenant_person.pk,
            "space": full_data["spaces"].pk,
            "type": "short_stay",
            "status": "reserved",
            "start_date": date.today().isoformat(),
            "end_date": (date.today() + timedelta(days=2)).isoformat(),
            "rent_amount": "80",
            "rent_period": "night",
            "currency": "CAD",
            "deposit_amount": "0",
        },
    )
    assert resp.status_code == 400
    assert b"overlap" in resp.content


def test_archive_keeps_record(staff, full_data):
    t = full_data["tickets"]
    resp = staff.post(reverse("web:archive", args=["tickets", t.pk]))
    assert resp.status_code == 302
    t.refresh_from_db()
    assert t.archived_at is not None
    assert staff.get(reverse("web:detail", args=["tickets", t.pk])).status_code == 404


def test_confirm_payment(staff, full_data):
    p = full_data["payments"]
    staff.post(reverse("web:payment-confirm", args=[p.pk]))
    p.refresh_from_db()
    assert p.status == "confirmed" and p.confirmed_by is not None


def test_tenant_cannot_open_staff_screens(ws, make_user, full_data):
    c = Client()
    c.force_login(make_user("sam", ws, "tenant"))
    resp = c.get(reverse("web:dashboard"))
    assert resp.status_code == 302


@pytest.fixture
def tenant_client(ws, make_user, full_data, tenant_person):
    user = make_user("sam", ws, "tenant")
    tenant_person.user = user
    tenant_person.save()
    c = Client()
    c.force_login(user)
    return c


def test_portal_home(tenant_client, ws, house):
    house["property"].house_info = {"wifi": "Oceanway-5G / pass1234"}
    house["property"].save()
    resp = tenant_client.get(reverse("portal:home"))
    assert resp.status_code == 200
    assert b"+1 604 555 0100" in resp.content and b"Oceanway-5G" in resp.content


def test_portal_urgent_ticket_with_photo(tenant_client, django_capture_on_commit_callbacks):
    photo = SimpleUploadedFile("leak.jpg", b"jpgdata", content_type="image/jpeg")
    with django_capture_on_commit_callbacks(execute=True):
        resp = tenant_client.post(
            reverse("portal:ticket-create"),
            {
                "category": "plumbing",
                "priority": "urgent",
                "title": "Water under the sink",
                "description": "",
                "photos": [photo],
            },
        )
    assert resp.status_code == 302
    ticket = Ticket.objects.get(title="Water under the sink")
    assert RecordFile.objects.filter(object_id=ticket.pk, content_type__model="ticket", kind="photo").exists()
    assert len(mail.outbox) == 1


def test_portal_invalid_ticket_shows_errors(tenant_client):
    resp = tenant_client.post(reverse("portal:ticket-create"), {"category": "plumbing", "priority": "urgent"})
    assert resp.status_code == 400


def test_portal_payment_proof(tenant_client, full_data):
    slip = SimpleUploadedFile("receipt.png", b"png", content_type="image/png")
    resp = tenant_client.post(
        reverse("portal:payment-proof"),
        {
            "amount": "1000",
            "paid_on": "2026-11-01",
            "purpose": "rent",
            "method": "e-transfer",
            "slip": [slip],
        },
    )
    assert resp.status_code == 302
    p = Payment.objects.get(notes__icontains="portal")
    assert p.status == "pending" and p.tenancy == full_data["tenancies"]


def test_staff_redirected_away_from_portal(staff):
    assert staff.get(reverse("portal:home")).status_code == 302
