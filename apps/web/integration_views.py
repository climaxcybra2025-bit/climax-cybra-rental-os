"""Staff actions for listings, channels, connections, channel replies, QuickBooks and API keys."""

import secrets
from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from apps.integrations import accounting, events
from apps.integrations.models import ApiKey, Connection, ExternalRef, ListingChannel, SyncLog
from apps.integrations.postkit import build_post
from apps.integrations.providers import quickbooks as qbo
from apps.integrations.providers.hostaway import HostawayClient, HostawayError
from apps.integrations.providers.telegram import TelegramClient, TelegramError
from apps.integrations.sync import ChannelError, post_listing_channel, reply_via_channel, sync_hostaway
from apps.records.models import Correspondence

ADMIN_ROLES = ("owner", "admin")


def detail_extras(request, slug, obj):
    """Extra context for record pages that have integration panels."""
    if slug == "listings":
        return {"post_text": build_post(obj), "extra_template": "web/includes/listing_extra.html"}
    if slug == "listing-channels":
        return {
            "post_text": build_post(obj.listing, obj.channel),
            "extra_template": "web/includes/listing_channel_extra.html",
        }
    if slug == "connections":
        hook = ""
        if obj.provider == Connection.Provider.HOSTAWAY:
            hook = request.build_absolute_uri(reverse("hooks:hostaway", args=[obj.webhook_token]))
        return {
            "webhook_url": hook,
            "logs": SyncLog.objects.filter(connection=obj)[:30],
            "pending_events": obj.outbound_events.filter(delivered_at__isnull=True, failed_at__isnull=True).count(),
            "event_names": events.EVENTS if obj.provider == Connection.Provider.N8N else {},
            "qbo_connected": bool(obj.settings.get("refresh_token")),
            "qbo_ready": qbo.is_configured(),
            "qbo_pending": accounting.pending_payments(obj).count()
            if obj.provider == Connection.Provider.QUICKBOOKS
            else 0,
            "qbo_start": accounting.start_date(obj) if obj.provider == Connection.Provider.QUICKBOOKS else None,
            "extra_template": "web/includes/connection_extra.html",
        }
    if slug == "payments":
        refs = ExternalRef.objects.filter(
            workspace_id=obj.workspace_id,
            content_type__app_label="tenancies",
            content_type__model="payment",
            object_id=obj.pk,
        )
        return {"external_refs": refs, "extra_template": "web/includes/payment_extra.html" if refs else ""}
    if slug == "correspondence":
        can_reply = (obj.external_thread_id or "").startswith("hostaway:") and obj.direction == "in"
        return {"can_reply": can_reply, "extra_template": "web/includes/correspondence_extra.html" if can_reply else ""}
    return {}


class _Staff(View):
    admin_only = False

    def dispatch(self, request, *args, **kwargs):
        membership = getattr(request, "membership", None)
        if not request.user.is_authenticated or membership is None or not membership.is_staff_role:
            return redirect("home")
        if self.admin_only and membership.role not in ADMIN_ROLES:
            messages.error(request, _("Only the owner or an admin can do this."))
            return redirect("web:dashboard")
        return super().dispatch(request, *args, **kwargs)


class ConnectionTestView(_Staff):
    admin_only = True

    def post(self, request, pk):
        conn = get_object_or_404(Connection.objects.active(), pk=pk, workspace=request.workspace)
        try:
            if conn.provider == Connection.Provider.HOSTAWAY:
                count = len(HostawayClient(conn).listings())
                msg = _("Connected to Hostaway. %(n)s listings found.") % {"n": count}
            elif conn.provider == Connection.Provider.TELEGRAM:
                bot = TelegramClient(conn).me()
                msg = _("Connected as @%(bot)s.") % {"bot": bot.get("username", "?")}
            else:
                queued = events.emit(request.workspace, "connection.test", {"message": "Test from Rental OS"})
                sent, failed = events.deliver_due()
                msg = _("Test event sent to n8n.") if sent and not failed else _("Test event queued; delivery failed.")
                if not queued:
                    msg = _("Turn the connection on first.")
        except (HostawayError, TelegramError) as exc:
            conn.mark_error(exc)
            messages.error(request, str(exc))
        else:
            conn.mark_ok()
            messages.success(request, msg)
        return redirect("web:detail", resource="connections", pk=pk)


class ConnectionSyncView(_Staff):
    admin_only = True

    def post(self, request, pk):
        conn = get_object_or_404(
            Connection.objects.active(), pk=pk, workspace=request.workspace, provider=Connection.Provider.HOSTAWAY
        )
        try:
            imported, msgs = sync_hostaway(conn)
        except HostawayError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Synced %(r)s bookings and %(m)s new messages.") % {"r": imported, "m": msgs})
        return redirect("web:detail", resource="connections", pk=pk)


class ListingChannelPostView(_Staff):
    def post(self, request, pk):
        lc = get_object_or_404(ListingChannel.objects.active(), pk=pk, workspace=request.workspace)
        try:
            post_listing_channel(lc)
        except ChannelError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, _("Posted."))
        return redirect("web:detail", resource="listing-channels", pk=pk)


class ListingChannelMarkPostedView(_Staff):
    def post(self, request, pk):
        lc = get_object_or_404(ListingChannel.objects.active(), pk=pk, workspace=request.workspace)
        lc.status, lc.posted_at, lc.last_error = ListingChannel.Status.POSTED, timezone.now(), ""
        if request.POST.get("url", "").startswith("http"):
            lc.url = request.POST["url"][:200]
        lc.save()
        messages.success(request, _("Marked as posted."))
        return redirect("web:detail", resource="listing-channels", pk=pk)


class CorrespondenceReplyView(_Staff):
    def post(self, request, pk):
        c = get_object_or_404(Correspondence.objects.active(), pk=pk, workspace=request.workspace)
        text = request.POST.get("body", "").strip()
        if not text:
            messages.error(request, _("Write a reply first."))
        else:
            try:
                reply_via_channel(c, text, request.user)
            except ChannelError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Reply sent to the guest."))
        return redirect("web:detail", resource="correspondence", pk=pk)


class ApiKeysView(_Staff):
    admin_only = True

    def get(self, request, new_key=None):
        from .views import StaffRequiredMixin

        keys = ApiKey.objects.filter(workspace=request.workspace)
        ctx = StaffRequiredMixin().base_context(keys=keys, new_key=new_key)
        return render(request, "web/api_keys.html", ctx)

    def post(self, request):
        if request.POST.get("revoke"):
            key = get_object_or_404(ApiKey, pk=request.POST["revoke"], workspace=request.workspace)
            key.revoked_at = key.revoked_at or timezone.now()
            key.save(update_fields=["revoked_at"])
            messages.success(request, _("Key revoked."))
            return redirect("web:api-keys")
        name = request.POST.get("name", "").strip()[:120] or "n8n"
        _key, raw = ApiKey.issue(request.workspace, name, request.user)
        return self.get(request, new_key=raw)


# --- QuickBooks Online -----------------------------------------------------------------------------

QBO_STATE = "qbo_oauth_state"


class QuickBooksConnectView(_Staff):
    """Send the admin to Intuit to approve access to their QuickBooks company."""

    admin_only = True

    def post(self, request, pk):
        conn = get_object_or_404(
            Connection.objects.active(), pk=pk, workspace=request.workspace, provider=Connection.Provider.QUICKBOOKS
        )
        if not qbo.is_configured():
            messages.error(
                request, _("QuickBooks is not set up on this server yet (QBO_CLIENT_ID and QBO_CLIENT_SECRET).")
            )
            return redirect("web:detail", resource="connections", pk=pk)
        state = qbo.new_state()
        request.session[QBO_STATE] = {"state": state, "connection": conn.pk, "workspace": request.workspace.pk}
        return redirect(qbo.authorize_url(state))


class QuickBooksCallbackView(_Staff):
    admin_only = True

    def get(self, request):
        saved = request.session.pop(QBO_STATE, None) or {}
        state, code, realm = request.GET.get("state"), request.GET.get("code"), request.GET.get("realmId")
        if not saved or not state or not secrets.compare_digest(saved.get("state", ""), state):
            messages.error(request, _("The QuickBooks sign-in could not be verified. Try again."))
            return redirect("web:list", resource="connections")
        conn = get_object_or_404(
            Connection.objects.active(),
            pk=saved["connection"],
            workspace_id=saved["workspace"],
            provider=Connection.Provider.QUICKBOOKS,
        )
        if conn.workspace_id != request.workspace.pk:
            return redirect("home")
        if request.GET.get("error") or not code or not realm:
            messages.error(request, _("QuickBooks access was not granted."))
            return redirect("web:detail", resource="connections", pk=conn.pk)
        try:
            qbo.exchange_code(conn, code, realm)
            company = qbo.QuickBooksClient(conn).company()
            conn.settings = {**conn.settings, "company_name": company.get("CompanyName", "")}
            conn.save(update_fields=["settings", "updated_at"])
        except qbo.QuickBooksError as exc:
            conn.mark_error(exc)
            messages.error(request, str(exc))
        else:
            conn.mark_ok()
            messages.success(request, _("Connected to QuickBooks. Now choose where each kind of payment goes."))
        return redirect("web:qbo-mapping", pk=conn.pk)


class QuickBooksMappingView(_Staff):
    """Choose the QuickBooks product/service or account for each payment purpose, and the start date."""

    admin_only = True

    def _conn(self, request, pk):
        return get_object_or_404(
            Connection.objects.active(), pk=pk, workspace=request.workspace, provider=Connection.Provider.QUICKBOOKS
        )

    def get(self, request, pk):
        from apps.tenancies.models import Payment

        from .views import StaffRequiredMixin

        conn = self._conn(request, pk)
        try:
            client = qbo.QuickBooksClient(conn)
            items, accounts = client.items(), client.accounts()
        except qbo.QuickBooksError as exc:
            messages.error(request, str(exc))
            return redirect("web:detail", resource="connections", pk=pk)
        labels = dict(Payment.Purpose.choices)
        m = accounting.mapping(conn)
        banks = [a for a in accounts if a.get("AccountType") in ("Bank", "Other Current Asset", "Credit Card")]
        ctx = StaffRequiredMixin().base_context(
            obj=conn,
            items=sorted(items, key=lambda i: i["Name"]),
            accounts=sorted(accounts, key=lambda a: a["Name"]),
            banks=sorted(banks, key=lambda a: a["Name"]),
            in_rows=[(p, labels[p], (m.get("items") or {}).get(p, "")) for p in accounting.IN_PURPOSES],
            out_rows=[(p, labels[p], (m.get("expense_accounts") or {}).get(p, "")) for p in accounting.OUT_PURPOSES],
            mapping=m,
            pending=accounting.pending_payments(conn).count(),
        )
        return render(request, "web/qbo_mapping.html", ctx)

    def post(self, request, pk):
        conn = self._conn(request, pk)
        data = request.POST
        start = data.get("start_date", "")
        try:
            date.fromisoformat(start)
        except ValueError:
            start = ""
        conn.settings = {
            **conn.settings,
            "mapping": {
                "items": {p: data.get(f"item_{p}", "") for p in accounting.IN_PURPOSES if data.get(f"item_{p}")},
                "expense_accounts": {
                    p: data.get(f"account_{p}", "") for p in accounting.OUT_PURPOSES if data.get(f"account_{p}")
                },
                "deposit_account": data.get("deposit_account", ""),
                "payment_account": data.get("payment_account", ""),
                "start_date": start,
            },
        }
        conn.save(update_fields=["settings", "updated_at"])
        if start:
            messages.success(
                request, _("Saved. Confirmed payments from %(date)s on will be sent to QuickBooks.") % {"date": start}
            )
        else:
            messages.warning(request, _("Saved. Nothing is sent until you choose a start date."))
        return redirect("web:detail", resource="connections", pk=pk)


class QuickBooksPushView(_Staff):
    admin_only = True

    def post(self, request, pk):
        conn = get_object_or_404(
            Connection.objects.active(), pk=pk, workspace=request.workspace, provider=Connection.Provider.QUICKBOOKS
        )
        try:
            pushed, errors = accounting.push_pending(conn)
        except qbo.QuickBooksError as exc:
            messages.error(request, str(exc))
        else:
            if errors:
                messages.error(
                    request, _("%(n)s sent; some need attention: %(e)s") % {"n": pushed, "e": "; ".join(errors[:3])}
                )
            else:
                messages.success(request, _("%(n)s payments sent to QuickBooks.") % {"n": pushed})
        return redirect("web:detail", resource="connections", pk=pk)


class QuickBooksDisconnectView(_Staff):
    admin_only = True

    def post(self, request, pk):
        conn = get_object_or_404(
            Connection.objects.active(), pk=pk, workspace=request.workspace, provider=Connection.Provider.QUICKBOOKS
        )
        try:
            qbo.revoke(conn)
        except Exception:  # revoking is best effort; the tokens are deleted here either way
            conn.settings = {k: v for k, v in conn.settings.items() if "token" not in k}
            conn.save(update_fields=["settings", "updated_at"])
        messages.success(request, _("Disconnected from QuickBooks. Payments already sent stay in QuickBooks."))
        return redirect("web:detail", resource="connections", pk=pk)
