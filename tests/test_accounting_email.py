import base64
import hashlib
import hmac
import json
import time
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from django.test import Client
from django.urls import reverse

from apps.integrations import accounting
from apps.integrations.models import Connection, ExternalRef, Inbox
from apps.integrations.providers import quickbooks as qbo
from apps.landlords.models import LandlordRepresentative
from apps.records.models import Correspondence, RecordFile
from apps.tenancies.models import Payment, Tenancy

QBO = "https://sandbox-quickbooks.api.intuit.com/v3/company/4620816365"


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.QBO_CLIENT_ID = "cid"
    settings.QBO_CLIENT_SECRET = "csecret"
    settings.QBO_REDIRECT_URI = "https://app.example.com/app/quickbooks/callback/"
    settings.QBO_ENVIRONMENT = "sandbox"
    settings.INBOUND_EMAIL_DOMAIN = "in.example.com"
    settings.MAILGUN_WEBHOOK_SIGNING_KEY = "mg-key"
    settings.POSTMARK_INBOUND_TOKEN = "pm-token"


@pytest.fixture
def owner_client(ws, make_user):
    user = make_user("kimia", ws, "owner")
    user.email = "kimia@oceanway.ca"
    user.save()
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def qb(ws):
    conn = Connection.objects.create(workspace=ws, provider="quickbooks", name="QuickBooks")
    conn.account_id = "4620816365"
    qbo.store_tokens(conn, {"access_token": "acc", "refresh_token": "ref", "expires_in": 3600})
    conn.settings["mapping"] = {
        "items": {"rent": "1", "deposit": "2"},
        "expense_accounts": {"landlord_rent": "60"},
        "deposit_account": "35",
        "payment_account": "35",
        "start_date": str(date.today() - timedelta(days=30)),
    }
    conn.save()
    return conn


@pytest.fixture
def tenancy(ws, house, tenant_person):
    return Tenancy.objects.create(
        workspace=ws,
        space=house["room"],
        person=tenant_person,
        type="fixed_term",
        status="active",
        start_date=date.today() - timedelta(days=10),
        end_date=date.today() + timedelta(days=170),
        rent_amount=1000,
        rent_period="month",
        currency="CAD",
    )


def pay(ws, **kw):
    data = dict(
        workspace=ws,
        direction="in",
        purpose="rent",
        amount=1000,
        currency="CAD",
        paid_on=date.today(),
        status="confirmed",
        method="e-transfer",
        reference="CA1234",
    )
    data.update(kw)
    return Payment.objects.create(**data)


def qbo_query(entity, rows):
    return {"QueryResponse": {entity: rows} if rows else {}}


# --- QuickBooks -----------------------------------------------------------------------------------


def test_connect_redirects_to_intuit_and_callback_stores_tokens(ws, owner_client):
    conn = Connection.objects.create(workspace=ws, provider="quickbooks", name="QuickBooks")
    resp = owner_client.post(reverse("web:qbo-connect", args=[conn.pk]))
    assert resp.status_code == 302
    url = urlparse(resp["Location"])
    q = parse_qs(url.query)
    assert url.netloc == "appcenter.intuit.com" and q["scope"] == ["com.intuit.quickbooks.accounting"]
    state = q["state"][0]
    with responses.RequestsMock() as rsps:
        rsps.post(
            qbo.TOKEN_URL,
            json={
                "access_token": "a1",
                "refresh_token": "r1",
                "expires_in": 3600,
                "x_refresh_token_expires_in": 8726400,
            },
        )
        rsps.get(f"{QBO}/companyinfo/4620816365", json={"CompanyInfo": {"CompanyName": "Oceanway Living Inc."}})
        resp = owner_client.get(reverse("web:qbo-callback"), {"state": state, "code": "abc", "realmId": "4620816365"})
    assert resp.status_code == 302
    conn.refresh_from_db()
    assert conn.account_id == "4620816365" and conn.settings["company_name"] == "Oceanway Living Inc."
    assert "r1" not in json.dumps(conn.settings)  # tokens are encrypted


def test_callback_with_wrong_state_is_refused(ws, owner_client):
    conn = Connection.objects.create(workspace=ws, provider="quickbooks", name="QuickBooks")
    owner_client.post(reverse("web:qbo-connect", args=[conn.pk]))
    resp = owner_client.get(reverse("web:qbo-callback"), {"state": "forged", "code": "x", "realmId": "1"})
    assert resp.status_code == 302
    conn.refresh_from_db()
    assert not conn.account_id


@responses.activate
def test_rent_payment_becomes_sales_receipt_once(qb, tenancy, ws):
    p = pay(ws, tenancy=tenancy, period=date.today().replace(day=1))
    responses.get(f"{QBO}/query", json=qbo_query("Customer", []))
    responses.post(f"{QBO}/customer", json={"Customer": {"Id": "58", "DisplayName": "Sam Lee"}})
    responses.post(f"{QBO}/salesreceipt", json={"SalesReceipt": {"Id": "901"}})
    assert accounting.push_pending(qb) == (1, [])
    receipt = json.loads(responses.calls[-1].request.body)
    assert receipt["CustomerRef"] == {"value": "58"}
    assert receipt["Line"][0]["SalesItemLineDetail"]["ItemRef"] == {"value": "1"}
    assert receipt["Line"][0]["Amount"] == 1000.0 and receipt["DepositToAccountRef"] == {"value": "35"}
    assert "requestid=ros-" in responses.calls[-1].request.url
    assert "CurrencyRef" not in receipt
    assert ExternalRef.for_object(qb, p).external_id == "901"
    # A second run sends nothing.
    assert accounting.push_pending(qb) == (0, [])


@responses.activate
def test_landlord_rent_becomes_expense_with_vendor(qb, lease, ws):
    LandlordRepresentative.objects.create(workspace=ws, landlord=lease.landlord, name="Rep")
    pay(ws, direction="out", purpose="landlord_rent", lease=lease, amount=3200)
    responses.get(f"{QBO}/query", json=qbo_query("Vendor", [{"Id": "77", "DisplayName": "Owner One"}]))
    responses.post(f"{QBO}/purchase", json={"Purchase": {"Id": "450"}})
    assert accounting.push_pending(qb) == (1, [])
    body = json.loads(responses.calls[-1].request.body)
    assert body["EntityRef"] == {"value": "77", "type": "Vendor"} and body["AccountRef"] == {"value": "35"}
    assert body["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"] == {"value": "60"}


@responses.activate
def test_unmapped_purpose_is_reported_not_sent(qb, tenancy, ws):
    pay(ws, tenancy=tenancy, purpose="guest_fee", amount=50)
    pushed, errors = accounting.push_pending(qb)
    assert pushed == 0 and "Guest fee" in errors[0]
    assert len(responses.calls) == 0
    qb.refresh_from_db()
    assert qb.last_error


def test_payments_before_start_date_or_pending_are_not_sent(qb, tenancy, ws):
    pay(ws, tenancy=tenancy, paid_on=date.today() - timedelta(days=60))
    pay(ws, tenancy=tenancy, status="pending")
    assert accounting.pending_payments(qb).count() == 0


@responses.activate
def test_expired_access_token_is_refreshed(qb, tenancy, ws):
    qb.settings["access_expires_at"] = "2000-01-01T00:00:00+00:00"
    qb.save()
    pay(ws, tenancy=tenancy)
    responses.post(qbo.TOKEN_URL, json={"access_token": "new", "refresh_token": "ref2", "expires_in": 3600})
    responses.get(f"{QBO}/query", json=qbo_query("Customer", [{"Id": "58"}]))
    responses.post(f"{QBO}/salesreceipt", json={"SalesReceipt": {"Id": "902"}})
    assert accounting.push_pending(qb) == (1, [])
    assert responses.calls[1].request.headers["Authorization"] == "Bearer new"


@responses.activate
def test_duplicate_customer_name_gets_suffix(qb, tenancy, ws):
    pay(ws, tenancy=tenancy)
    responses.get(f"{QBO}/query", json=qbo_query("Customer", []))
    responses.post(
        f"{QBO}/customer",
        json={"Fault": {"Error": [{"code": "6240", "Message": "Duplicate Name Exists Error"}]}},
        status=400,
    )
    responses.post(f"{QBO}/customer", json={"Customer": {"Id": "59"}})
    responses.post(f"{QBO}/salesreceipt", json={"SalesReceipt": {"Id": "903"}})
    assert accounting.push_pending(qb) == (1, [])
    second = json.loads(responses.calls[2].request.body)
    assert second["DisplayName"].endswith(f"#{tenancy.person.pk}")


@responses.activate
def test_mapping_page_lists_items_and_saves(qb, owner_client):
    responses.get(f"{QBO}/query", json=qbo_query("Item", [{"Id": "1", "Name": "Room rent"}]))
    responses.get(f"{QBO}/query", json=qbo_query("Account", [{"Id": "35", "Name": "Chequing", "AccountType": "Bank"}]))
    resp = owner_client.get(reverse("web:qbo-mapping", args=[qb.pk]))
    assert resp.status_code == 200 and b"Room rent" in resp.content and b"Chequing" in resp.content
    owner_client.post(
        reverse("web:qbo-mapping", args=[qb.pk]),
        {"item_rent": "1", "deposit_account": "35", "payment_account": "35", "start_date": "2026-11-01"},
    )
    qb.refresh_from_db()
    assert accounting.mapping(qb)["items"] == {"rent": "1"} and accounting.start_date(qb) == date(2026, 11, 1)


def test_connection_page_for_quickbooks(qb, owner_client):
    resp = owner_client.get(reverse("web:detail", args=["connections", qb.pk]))
    assert resp.status_code == 200 and b"Send payments now" in resp.content


# --- inbound email --------------------------------------------------------------------------------


@pytest.fixture
def inbox(ws, house):
    return Inbox.objects.create(workspace=ws, address="regan-st", property=house["property"])


def mailgun_post(client, **fields):
    ts, token = str(int(time.time())), "tok123"
    sig = hmac.new(b"mg-key", f"{ts}{token}".encode(), hashlib.sha256).hexdigest()
    data = {"timestamp": ts, "token": token, "signature": sig, **fields}
    return client.post(reverse("hooks:email-mailgun"), data)


def test_mailgun_email_from_landlord_is_filed_with_attachment(inbox, lease, client):
    lease.landlord.email = "owner@example.com"
    lease.landlord.save()
    from django.core.files.uploadedfile import SimpleUploadedFile

    resp = mailgun_post(
        client,
        recipient="regan-st@in.example.com",
        sender="owner@example.com",
        **{
            "from": "Owner One <owner@example.com>",
            "subject": "Re: Water heater",
            "body-plain": "Plumber Tuesday.",
            "Message-Id": "<abc@mail.example.com>",
        },
        **{"attachment-1": SimpleUploadedFile("invoice.pdf", b"%PDF-1.4", content_type="application/pdf")},
    )
    assert resp.status_code == 200, resp.content
    c = Correspondence.objects.get()
    assert c.counterpart == "landlord" and c.landlord == lease.landlord and c.property == inbox.property
    assert c.subject == "Water heater" and c.status == "needs_reply"
    f = RecordFile.objects.get()
    assert f.kind == "letter" and f.object_id == c.pk


def test_mailgun_bad_signature_is_rejected(inbox, client):
    resp = client.post(reverse("hooks:email-mailgun"), {"timestamp": "1", "token": "t", "signature": "x"})
    assert resp.status_code == 403 and not Correspondence.objects.exists()


def test_duplicate_email_is_filed_once(inbox, client):
    fields = {
        "recipient": "regan-st@in.example.com",
        "sender": "a@b.com",
        "subject": "Hi",
        "body-plain": "x",
        "Message-Id": "<same@x>",
    }
    r = mailgun_post(client, **fields)
    assert r.status_code == 200, r.content
    mailgun_post(client, **fields)
    assert Correspondence.objects.count() == 1


def test_unknown_inbox_is_refused(inbox, client):
    resp = mailgun_post(client, recipient="nobody@in.example.com", sender="a@b.com", subject="Hi")
    assert resp.status_code == 406


def test_postmark_forwarded_city_email(inbox, client, owner_client):
    payload = {
        "FromFull": {"Email": "kimia@oceanway.ca", "Name": "Kimia"},
        "ToFull": [{"Email": "regan-st+city@in.example.com"}],
        "Subject": "Fwd: Business licence renewal",
        "TextBody": "---------- Forwarded message ---------\nFrom: Licensing <licences@coquitlam.ca>\nRenew by Dec 1.",
        "Headers": [{"Name": "Message-ID", "Value": "<fwd1@mail.gmail.com>"}],
        "Date": "Wed, 4 Nov 2026 10:00:00 -0800",
        "Attachments": [
            {
                "Name": "notice.png",
                "ContentType": "image/png",
                "Content": base64.b64encode(b"\x89PNG").decode(),
                "ContentLength": 4,
            }
        ],
    }
    resp = client.post(
        reverse("hooks:email-postmark", args=["pm-token"]), data=json.dumps(payload), content_type="application/json"
    )
    assert resp.status_code == 200, resp.content
    c = Correspondence.objects.get()
    assert "licences@coquitlam.ca" in c.counterpart_name  # original sender, not the staff member who forwarded
    assert c.subject == "Business licence renewal"
    assert RecordFile.objects.get().kind == "photo"


def test_postmark_wrong_token(inbox, client):
    resp = client.post(reverse("hooks:email-postmark", args=["nope"]), data="{}", content_type="application/json")
    assert resp.status_code == 403


def test_government_sender_is_classified(inbox, client):
    mailgun_post(
        client,
        recipient="regan-st@in.example.com",
        sender="tax@cra-arc.gc.ca",
        subject="Notice",
        **{"Message-Id": "<gov@x>"},
    )
    assert Correspondence.objects.get().counterpart == "government"
