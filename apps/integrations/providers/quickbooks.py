"""QuickBooks Online client (Intuit OAuth 2.0 + Accounting API v3).

One Intuit developer app serves every workspace: its client ID and secret
come from settings (QBO_CLIENT_ID / QBO_CLIENT_SECRET). Each workspace
connects its own company; the realm (company) ID goes in
``Connection.account_id`` and tokens are stored encrypted in
``Connection.settings``. Access tokens last an hour and are refreshed
automatically; the refresh token rolls forward on every refresh.
"""

import base64
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .. import crypto

AUTHORIZE_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"
SCOPE = "com.intuit.quickbooks.accounting"
API_BASE = {
    "production": "https://quickbooks.api.intuit.com",
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
}
MINOR_VERSION = "75"


class QuickBooksError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def is_configured():
    return bool(settings.QBO_CLIENT_ID and settings.QBO_CLIENT_SECRET and settings.QBO_REDIRECT_URI)


def authorize_url(state):
    return (
        AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "client_id": settings.QBO_CLIENT_ID,
                "response_type": "code",
                "scope": SCOPE,
                "redirect_uri": settings.QBO_REDIRECT_URI,
                "state": state,
            }
        )
    )


def new_state():
    return secrets.token_urlsafe(24)


def _basic():
    raw = f"{settings.QBO_CLIENT_ID}:{settings.QBO_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _token_request(data, session=None):
    http = session or requests
    resp = http.post(
        TOKEN_URL,
        data=data,
        headers={"Authorization": _basic(), "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise QuickBooksError(f"Intuit sign-in failed ({resp.status_code}): {resp.text[:200]}", code="auth")
    return resp.json()


def store_tokens(connection, tokens):
    now = timezone.now()
    connection.settings = {
        **connection.settings,
        "access_token": crypto.encrypt(tokens["access_token"]),
        "refresh_token": crypto.encrypt(tokens["refresh_token"]),
        "access_expires_at": (now + timedelta(seconds=int(tokens.get("expires_in", 3600)) - 120)).isoformat(),
        "refresh_expires_at": (
            now + timedelta(seconds=int(tokens.get("x_refresh_token_expires_in", 8640000)))
        ).isoformat(),
    }


def exchange_code(connection, code, realm_id, session=None):
    tokens = _token_request(
        {"grant_type": "authorization_code", "code": code, "redirect_uri": settings.QBO_REDIRECT_URI}, session
    )
    connection.account_id = realm_id
    store_tokens(connection, tokens)
    connection.save()
    return connection


class QuickBooksClient:
    def __init__(self, connection, session=None):
        if not connection.account_id or not connection.settings.get("refresh_token"):
            raise QuickBooksError("Connect this workspace to QuickBooks first.", code="not_connected")
        self.connection = connection
        self.http = session or requests.Session()
        env = settings.QBO_ENVIRONMENT if settings.QBO_ENVIRONMENT in API_BASE else "production"
        self.base = f"{API_BASE[env]}/v3/company/{connection.account_id}"

    def _access_token(self, force=False):
        s = self.connection.settings
        expires = parse_datetime(s.get("access_expires_at") or "")
        if not force and expires and expires > timezone.now():
            token = crypto.decrypt(s.get("access_token", ""))
            if token:
                return token
        refresh = crypto.decrypt(s.get("refresh_token", ""))
        if not refresh:
            raise QuickBooksError("QuickBooks sign-in expired. Connect again.", code="auth")
        tokens = _token_request({"grant_type": "refresh_token", "refresh_token": refresh}, self.http)
        store_tokens(self.connection, tokens)
        self.connection.save(update_fields=["settings", "updated_at"])
        return tokens["access_token"]

    def request(self, method, path, *, params=None, json=None, request_id=None):
        params = {"minorversion": MINOR_VERSION, **(params or {})}
        if request_id:
            params["requestid"] = request_id  # Intuit returns the original result for a repeated request ID
        for attempt in (0, 1):
            resp = self.http.request(
                method,
                f"{self.base}/{path}",
                params=params,
                json=json,
                headers={
                    "Authorization": f"Bearer {self._access_token(force=attempt == 1)}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            if resp.status_code == 401 and attempt == 0:
                continue
            body = resp.json() if resp.content else {}
            fault = body.get("Fault") or (body.get("fault") if isinstance(body.get("fault"), dict) else None)
            if resp.status_code >= 400 or fault:
                errors = (fault or {}).get("Error") or (fault or {}).get("error") or [{}]
                first = errors[0]
                msg = first.get("Detail") or first.get("Message") or first.get("message") or resp.text[:200]
                raise QuickBooksError(f"QuickBooks: {msg}", code=str(first.get("code") or resp.status_code))
            return body
        raise QuickBooksError("QuickBooks sign-in expired. Connect again.", code="auth")

    def query(self, sql):
        body = self.request("GET", "query", params={"query": sql})
        return body.get("QueryResponse", {})

    def company(self):
        return self.request("GET", f"companyinfo/{self.connection.account_id}").get("CompanyInfo", {})

    def items(self):
        return self.query("select Id, Name, Type, Active from Item where Active = true maxresults 1000").get("Item", [])

    def accounts(self):
        return self.query(
            "select Id, Name, AccountType, Classification from Account where Active = true maxresults 1000"
        ).get("Account", [])

    def find_by_name(self, entity, name):
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        rows = self.query(f"select Id, DisplayName from {entity} where DisplayName = '{safe}'").get(entity, [])
        return rows[0]["Id"] if rows else None

    def create(self, entity, payload, request_id=None):
        return self.request("POST", entity.lower(), json=payload, request_id=request_id)[entity]


def revoke(connection, session=None):
    http = session or requests
    refresh = crypto.decrypt(connection.settings.get("refresh_token", ""))
    if refresh:
        http.post(
            REVOKE_URL,
            json={"token": refresh},
            headers={"Authorization": _basic(), "Accept": "application/json"},
            timeout=20,
        )
    connection.settings = {
        k: v
        for k, v in connection.settings.items()
        if k not in {"access_token", "refresh_token", "access_expires_at", "refresh_expires_at"}
    }
    connection.save(update_fields=["settings", "updated_at"])
