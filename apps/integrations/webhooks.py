"""Inbound webhooks. The URL carries a long random token per connection."""

import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import inbound_email
from .models import Connection, SyncLog
from .providers.hostaway import HostawayError
from .sync import handle_hostaway_webhook

logger = logging.getLogger(__name__)
MAX_BODY = 512 * 1024


@csrf_exempt
@require_POST
def hostaway(request, token):
    conn = get_object_or_404(
        Connection.objects.active(), webhook_token=token, provider=Connection.Provider.HOSTAWAY, is_enabled=True
    )
    if len(request.body) > MAX_BODY:
        return HttpResponse(status=413)
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseBadRequest("invalid json")
    if not isinstance(payload, dict):
        return HttpResponseBadRequest("expected an object")
    try:
        kind, count = handle_hostaway_webhook(conn, payload)
    except HostawayError as exc:
        conn.mark_error(exc)
        SyncLog.objects.create(
            workspace_id=conn.workspace_id,
            connection=conn,
            direction="in",
            kind="webhook",
            status="error",
            message=str(exc)[:4000],
        )
        return JsonResponse({"ok": False}, status=502)  # Hostaway retries
    return JsonResponse({"ok": True, "kind": kind, "count": count})


MAX_EMAIL = 40 * 1024 * 1024


@csrf_exempt
@require_POST
def mailgun_email(request):
    """Mailgun route action: forward("https://<host>/hooks/email/mailgun/")."""
    post = request.POST
    if not inbound_email.mailgun_signature_ok(post.get("timestamp"), post.get("token"), post.get("signature")):
        return HttpResponse(status=403)
    parsed = inbound_email.parse_mailgun(post, request.FILES)
    c, created = inbound_email.file_email(parsed)
    # 406 tells Mailgun not to retry an email no inbox wants; 200 for everything else.
    if c is None:
        return HttpResponse("no inbox", status=406)
    return JsonResponse({"ok": True, "id": c.pk, "created": created})


@csrf_exempt
@require_POST
def postmark_email(request, token):
    """Postmark inbound webhook URL: https://<host>/hooks/email/postmark/<POSTMARK_INBOUND_TOKEN>/"""
    expected = settings.POSTMARK_INBOUND_TOKEN
    if not expected or not hmac.compare_digest(expected, token):
        return HttpResponse(status=403)
    if len(request.body) > MAX_EMAIL:
        return HttpResponse(status=413)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseBadRequest("invalid json")
    c, created = inbound_email.file_email(inbound_email.parse_postmark(data))
    if c is None:
        return JsonResponse({"ok": False, "reason": "no inbox"})  # 200 so Postmark does not retry
    return JsonResponse({"ok": True, "id": c.pk, "created": created})
