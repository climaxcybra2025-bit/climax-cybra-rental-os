"""Hostaway channel manager client.

Hostaway connects to Airbnb, Booking.com and Vrbo with their official APIs, so
Rental OS talks to Hostaway only. Auth is OAuth2 client credentials: the
Hostaway account ID is the client ID and the API key is the client secret.
The access token is cached (encrypted) on the connection.

Docs: https://api.hostaway.com/documentation (base URL https://api.hostaway.com/v1).
"""

import json

import requests

from .. import crypto

BASE_URL = "https://api.hostaway.com/v1"


class HostawayError(Exception):
    pass


class HostawayClient:
    def __init__(self, connection, session=None):
        self.connection = connection
        self.http = session or requests.Session()

    # --- auth ---------------------------------------------------------------------------------
    def _token(self, refresh=False):
        cached = crypto.decrypt(self.connection.settings.get("access_token", ""))
        if cached and not refresh:
            return cached
        if not self.connection.account_id or not self.connection.has_secret:
            raise HostawayError("Add the Hostaway account ID and API key to this connection first.")
        resp = self.http.post(
            f"{BASE_URL}/accessTokens",
            data={
                "grant_type": "client_credentials",
                "client_id": self.connection.account_id,
                "client_secret": self.connection.secret,
                "scope": "general",
            },
            headers={"Cache-Control": "no-cache"},
            timeout=20,
        )
        if resp.status_code >= 400:
            raise HostawayError(f"Hostaway rejected the account ID or API key ({resp.status_code}).")
        token = resp.json().get("access_token")
        if not token:
            raise HostawayError("Hostaway did not return an access token.")
        self.connection.settings = {**self.connection.settings, "access_token": crypto.encrypt(token)}
        self.connection.save(update_fields=["settings", "updated_at"])
        return token

    def _request(self, method, path, **kwargs):
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self._token(refresh=attempt == 1)}", "Cache-Control": "no-cache"}
            resp = self.http.request(method, f"{BASE_URL}{path}", headers=headers, timeout=30, **kwargs)
            if resp.status_code == 403 and attempt == 0:
                continue  # token expired or revoked: get a new one once
            if resp.status_code >= 400:
                raise HostawayError(f"Hostaway {method} {path} failed ({resp.status_code}): {resp.text[:300]}")
            body = resp.json()
            if body.get("status") not in (None, "success"):
                raise HostawayError(f"Hostaway {method} {path}: {json.dumps(body)[:300]}")
            return body.get("result")
        raise HostawayError("Hostaway authentication failed.")

    # --- API ----------------------------------------------------------------------------------
    def listings(self):
        return self._paged("/listings")

    def reservation(self, reservation_id):
        return self._request("GET", f"/reservations/{int(reservation_id)}")

    def reservations(self, limit=100, max_pages=10):
        """Most recently changed reservations first."""
        return self._paged(
            "/reservations", params={"sortOrder": "latestActivityDesc"}, limit=limit, max_pages=max_pages
        )

    def conversations(self, reservation_id):
        return self._request("GET", "/conversations", params={"reservationId": int(reservation_id)}) or []

    def messages(self, conversation_id, limit=50):
        return self._request("GET", f"/conversations/{int(conversation_id)}/messages", params={"limit": limit}) or []

    def send_message(self, conversation_id, body):
        return self._request(
            "POST",
            f"/conversations/{int(conversation_id)}/messages",
            json={"body": body, "communicationType": "channel"},
        )

    def _paged(self, path, params=None, limit=100, max_pages=20):
        out = []
        for page in range(max_pages):
            batch = self._request("GET", path, params={**(params or {}), "limit": limit, "offset": page * limit}) or []
            out.extend(batch)
            if len(batch) < limit:
                break
        return out


CANCELLED_STATUSES = {"cancelled", "declined", "expired", "inquiryDenied", "inquiryTimedout", "inquiryNotPossible"}
SKIP_STATUSES = {"inquiry", "inquiryPreapproved", "unknown"}


def channel_slug(channel_name):
    name = (channel_name or "").lower()
    if "airbnb" in name:
        return "airbnb"
    if "booking" in name:
        return "booking"
    if "vrbo" in name or "homeaway" in name:
        return "vrbo"
    return "direct" if not name or "direct" in name else name[:40]
