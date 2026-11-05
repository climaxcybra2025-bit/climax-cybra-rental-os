"""Push confirmed payments to QuickBooks Online.

- Money in (rent, deposits, fees) becomes a Sales Receipt for the tenant as customer,
  with the QuickBooks product/service chosen for that purpose.
- Money out (rent and deposits to landlords, refunds) becomes an Expense (Purchase)
  against the account chosen for that purpose, with the landlord as vendor.

Each payment is pushed once: an ExternalRef records the QuickBooks ID, and the
request carries a request ID so a retry after a timeout cannot create a duplicate.
Only payments dated on or after the connection's start date are pushed.
"""

import logging
from datetime import date

from django.utils import timezone

from apps.tenancies.models import Payment

from .models import Connection, ExternalRef, SyncLog
from .providers.quickbooks import QuickBooksClient, QuickBooksError

logger = logging.getLogger(__name__)

IN_PURPOSES = ["rent", "deposit", "guest_fee", "cleaning", "damage", "cancellation", "other"]
OUT_PURPOSES = ["landlord_rent", "landlord_deposit", "refund", "other"]
DUPLICATE_NAME = "6240"


def mapping(connection):
    return connection.settings.get("mapping", {})


def start_date(connection):
    raw = mapping(connection).get("start_date")
    return date.fromisoformat(raw) if raw else None


def _ensure_party(connection, client, obj, entity, display, extra):
    ref = ExternalRef.for_object(connection, obj)
    if ref:
        return ref.external_id
    qb_id = client.find_by_name(entity, display)
    if not qb_id:
        payload = {"DisplayName": display[:100], **extra}
        try:
            qb_id = client.create(entity, payload)["Id"]
        except QuickBooksError as exc:
            if exc.code != DUPLICATE_NAME:  # the name is taken by another kind of record (e.g. a vendor)
                raise
            payload["DisplayName"] = f"{display[:90]} #{obj.pk}"
            qb_id = client.create(entity, payload)["Id"]
    ExternalRef.link(connection, obj, entity, qb_id)
    return qb_id


def ensure_customer(connection, client, person):
    extra = {"GivenName": person.first_name[:100], "FamilyName": person.last_name[:100]}
    if person.email:
        extra["PrimaryEmailAddr"] = {"Address": person.email}
    if person.phone:
        extra["PrimaryPhone"] = {"FreeFormNumber": person.phone}
    return _ensure_party(connection, client, person, "Customer", str(person).strip() or f"Tenant {person.pk}", extra)


def ensure_vendor(connection, client, landlord):
    extra = {}
    if landlord.email:
        extra["PrimaryEmailAddr"] = {"Address": landlord.email}
    if getattr(landlord, "phone", ""):
        extra["PrimaryPhone"] = {"FreeFormNumber": landlord.phone}
    return _ensure_party(connection, client, landlord, "Vendor", landlord.name, extra)


def _description(p):
    where = ""
    if p.tenancy_id:
        where = f" – {p.tenancy.space.name}, {p.tenancy.space.property}"
    elif p.lease_id:
        where = f" – {p.lease.property}"
    period = f" ({p.period:%Y-%m})" if p.period else ""
    return f"{p.get_purpose_display()}{period}{where}"[:4000]


def _currency(p, payload):
    if p.currency and p.currency != p.workspace.base_currency:
        payload["CurrencyRef"] = {"value": p.currency}


def build_sales_receipt(connection, client, p):
    m = mapping(connection)
    item = (m.get("items") or {}).get(p.purpose)
    if not item:
        raise QuickBooksError(f"Choose a QuickBooks product/service for '{p.get_purpose_display()}'.", code="mapping")
    if not p.tenancy_id:
        raise QuickBooksError("Money received must be linked to a tenancy.", code="mapping")
    amount = float(p.amount)
    payload = {
        "TxnDate": p.paid_on.isoformat(),
        "CustomerRef": {"value": ensure_customer(connection, client, p.tenancy.person)},
        "PrivateNote": f"Rental OS payment #{p.pk}. {p.method} {p.reference}".strip()[:4000],
        "Line": [
            {
                "Amount": amount,
                "DetailType": "SalesItemLineDetail",
                "Description": _description(p),
                "SalesItemLineDetail": {"ItemRef": {"value": item}, "Qty": 1, "UnitPrice": amount},
            }
        ],
    }
    if p.reference:
        payload["PaymentRefNum"] = p.reference[:21]
    if m.get("deposit_account"):
        payload["DepositToAccountRef"] = {"value": m["deposit_account"]}
    _currency(p, payload)
    return "SalesReceipt", payload


def build_purchase(connection, client, p):
    m = mapping(connection)
    account = (m.get("expense_accounts") or {}).get(p.purpose)
    if not account:
        raise QuickBooksError(f"Choose a QuickBooks account for '{p.get_purpose_display()}'.", code="mapping")
    if not m.get("payment_account"):
        raise QuickBooksError("Choose the bank account that payments to landlords come from.", code="mapping")
    payload = {
        "TxnDate": p.paid_on.isoformat(),
        "PaymentType": "Check" if (p.method or "").lower().startswith("cheque") else "Cash",
        "AccountRef": {"value": m["payment_account"]},
        "PrivateNote": f"Rental OS payment #{p.pk}. {p.method} {p.reference}".strip()[:4000],
        "Line": [
            {
                "Amount": float(p.amount),
                "DetailType": "AccountBasedExpenseLineDetail",
                "Description": _description(p),
                "AccountBasedExpenseLineDetail": {"AccountRef": {"value": account}},
            }
        ],
    }
    if p.lease_id:
        payload["EntityRef"] = {"value": ensure_vendor(connection, client, p.lease.landlord), "type": "Vendor"}
    elif p.tenancy_id:
        payload["EntityRef"] = {"value": ensure_customer(connection, client, p.tenancy.person), "type": "Customer"}
    _currency(p, payload)
    return "Purchase", payload


def push_payment(connection, p, client):
    """Push one confirmed payment. Returns the ExternalRef (existing or new)."""
    existing = ExternalRef.for_object(connection, p)
    if existing:
        return existing
    entity, payload = (build_sales_receipt if p.direction == "in" else build_purchase)(connection, client, p)
    result = client.create(entity, payload, request_id=f"ros-{connection.pk}-pay-{p.pk}")
    ref = ExternalRef.link(connection, p, entity, result["Id"])
    SyncLog.objects.create(
        workspace_id=connection.workspace_id,
        connection=connection,
        direction="out",
        kind=f"qbo.{entity}",
        idempotency_key=f"payment:{p.pk}",
        message=f"Payment #{p.pk} → {entity} {result['Id']}",
    )
    return ref


def pending_payments(connection):
    since = start_date(connection)
    if since is None:
        return Payment.objects.none()
    synced = ExternalRef.objects.filter(
        connection=connection, content_type__app_label="tenancies", content_type__model="payment"
    ).values("object_id")
    return (
        Payment.objects.active()
        .filter(workspace_id=connection.workspace_id, status=Payment.Status.CONFIRMED, paid_on__gte=since)
        .exclude(pk__in=synced)
        .select_related(
            "tenancy__person", "tenancy__space__property", "lease__landlord", "lease__property", "workspace"
        )
        .order_by("paid_on", "pk")
    )


def push_pending(connection, client=None, limit=100):
    """Push every confirmed, not yet synced payment. Returns (pushed, errors)."""
    client = client or QuickBooksClient(connection)
    pushed, errors = 0, []
    for p in pending_payments(connection)[:limit]:
        try:
            push_payment(connection, p, client)
            pushed += 1
        except QuickBooksError as exc:
            errors.append(f"#{p.pk}: {exc}")
            SyncLog.objects.create(
                workspace_id=connection.workspace_id,
                connection=connection,
                direction="out",
                kind="qbo.payment",
                status="error",
                idempotency_key=f"payment:{p.pk}",
                message=str(exc)[:4000],
            )
            if exc.code == "auth":
                break
    if errors:
        connection.mark_error("; ".join(errors)[:2000])
    else:
        connection.last_success_at = timezone.now()
        connection.last_error = ""
        connection.save(update_fields=["last_success_at", "last_error", "updated_at"])
    return pushed, errors


def push_all():
    results = {}
    for conn in Connection.objects.active().filter(provider=Connection.Provider.QUICKBOOKS, is_enabled=True):
        if not conn.settings.get("refresh_token") or not start_date(conn):
            continue
        try:
            results[conn.pk] = push_pending(conn)
        except QuickBooksError as exc:
            conn.mark_error(exc)
            results[conn.pk] = str(exc)
        except Exception:
            logger.exception("QuickBooks push failed for connection %s", conn.pk)
    return results
