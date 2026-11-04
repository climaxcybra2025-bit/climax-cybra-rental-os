"""Staff actions for listings, channels, connections, channel replies and API keys."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from apps.integrations import events
from apps.integrations.models import ApiKey, Connection, ListingChannel, SyncLog
from apps.integrations.postkit import build_post
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
            "extra_template": "web/includes/connection_extra.html",
        }
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
