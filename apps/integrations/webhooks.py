"""Inbound webhooks. The URL carries a long random token per connection."""

import json
import logging

from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

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
