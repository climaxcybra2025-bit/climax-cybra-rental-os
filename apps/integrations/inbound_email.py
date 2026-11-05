"""File inbound emails (from Mailgun or Postmark) as Correspondence with attachments.

Who sent it decides the counterpart: a known landlord or landlord
representative, a tenant, a government address, or the inbox default. When a
staff member forwards an email, the original sender is read from the
forwarded block.
"""

import base64
import hashlib
import hmac
import re
import time
from dataclasses import dataclass, field
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.html import strip_tags

from apps.landlords.models import Landlord, LandlordRepresentative
from apps.people.models import Person
from apps.records.models import Correspondence, RecordFile
from apps.workspaces.models import Membership

from .models import Inbox, SyncLog

MAX_BODY = 100_000
MAX_ATTACHMENTS = 20
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
GOV_SUFFIXES = (".gov", ".gc.ca", ".gov.bc.ca", ".gov.on.ca", ".go.jp", ".lg.jp", ".gov.uk", ".gov.au")
FORWARD_FROM = re.compile(r"^\s*(?:\*?From|De|Von|از)\*?:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


@dataclass
class ParsedEmail:
    recipients: list
    sender: str
    sender_name: str = ""
    subject: str = ""
    text: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    date: object = None
    attachments: list = field(default_factory=list)  # (filename, content_type, bytes)


# --- parsing ---------------------------------------------------------------------------------------


def _clean_id(value):
    return (value or "").strip().strip("<>")[:250]


def _date(value):
    try:
        d = parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        d = None
    return d or timezone.now()


def parse_mailgun(post, files):
    headers = {}
    try:
        import json

        headers = {k.lower(): v for k, v in json.loads(post.get("message-headers", "[]"))}
    except ValueError:
        pass
    name, sender = parseaddr(post.get("from", "") or post.get("sender", ""))
    recipients = []
    for value in (post.get("recipient", ""), headers.get("to", ""), headers.get("cc", "")):
        if value:  # getaddresses() gives up on the whole list if one entry is empty
            recipients += [a for _n, a in getaddresses([value])]
    attachments = []
    for key in sorted(files):
        f = files[key]
        if f.size <= MAX_ATTACHMENT_BYTES:
            attachments.append((f.name, f.content_type, f.read()))
    text = post.get("body-plain") or strip_tags(post.get("body-html", ""))
    return ParsedEmail(
        recipients=[r for r in recipients if r],
        sender=post.get("sender") or sender,
        sender_name=name,
        subject=post.get("subject", ""),
        text=text,
        message_id=_clean_id(post.get("Message-Id") or headers.get("message-id")),
        in_reply_to=_clean_id(post.get("In-Reply-To") or headers.get("in-reply-to")),
        date=_date(post.get("Date") or headers.get("date")),
        attachments=attachments[:MAX_ATTACHMENTS],
    )


def parse_postmark(data):
    headers = {h.get("Name", "").lower(): h.get("Value", "") for h in data.get("Headers") or []}
    sender = (data.get("FromFull") or {}).get("Email") or parseaddr(data.get("From", ""))[1]
    recipients = [r.get("Email", "") for key in ("ToFull", "CcFull", "BccFull") for r in data.get(key) or []]
    if data.get("OriginalRecipient"):
        recipients.insert(0, data["OriginalRecipient"])
    attachments = []
    for a in data.get("Attachments") or []:
        if int(a.get("ContentLength") or 0) > MAX_ATTACHMENT_BYTES:
            continue
        try:
            attachments.append(
                (a.get("Name") or "attachment", a.get("ContentType", ""), base64.b64decode(a["Content"]))
            )
        except (KeyError, ValueError):
            continue
    return ParsedEmail(
        recipients=[r for r in recipients if r],
        sender=sender,
        sender_name=(data.get("FromFull") or {}).get("Name", ""),
        subject=data.get("Subject", ""),
        text=data.get("TextBody") or strip_tags(data.get("HtmlBody", "")),
        message_id=_clean_id(headers.get("message-id") or data.get("MessageID")),
        in_reply_to=_clean_id(headers.get("in-reply-to")),
        date=_date(data.get("Date")),
        attachments=attachments[:MAX_ATTACHMENTS],
    )


# --- verification ----------------------------------------------------------------------------------


def mailgun_signature_ok(timestamp, token, signature, max_age=3600):
    key = settings.MAILGUN_WEBHOOK_SIGNING_KEY
    if not key or not timestamp or not token or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > max_age:
            return False
    except ValueError:
        return False
    expected = hmac.new(key.encode(), f"{timestamp}{token}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# --- filing ----------------------------------------------------------------------------------------


def find_inbox(recipients):
    domain = (settings.INBOUND_EMAIL_DOMAIN or "").lower()
    for addr in recipients:
        local, _, dom = addr.lower().partition("@")
        if domain and dom != domain:
            continue
        inbox = Inbox.objects.active().filter(address=local.split("+", 1)[0], is_enabled=True).first()
        if inbox:
            return inbox
    return None


def _is_staff_sender(workspace, email):
    user = get_user_model().objects.filter(email__iexact=email).first()
    return (
        bool(user)
        and Membership.objects.filter(
            workspace=workspace, user=user, role__in=["owner", "admin", "partner", "team"]
        ).exists()
    )


def original_sender(text):
    m = FORWARD_FROM.search(text or "")
    if not m:
        return "", ""
    name, addr = parseaddr(m.group(1).strip())
    return name, addr


def classify(workspace, email, inbox):
    """Return (counterpart, landlord, person) for a sender address."""
    email = (email or "").lower()
    landlord = Landlord.objects.active().filter(workspace=workspace, email__iexact=email).first()
    if landlord is None:
        rep = (
            LandlordRepresentative.objects.active()
            .filter(workspace=workspace, email__iexact=email)
            .select_related("landlord")
            .first()
        )
        landlord = rep.landlord if rep else None
    if landlord:
        return Correspondence.Counterpart.LANDLORD, landlord, None
    person = Person.objects.active().filter(workspace=workspace, email__iexact=email).first() if email else None
    if person:
        return Correspondence.Counterpart.TENANT, None, person
    domain = email.rsplit("@", 1)[-1]
    if domain.endswith(GOV_SUFFIXES):
        return Correspondence.Counterpart.GOVERNMENT, None, None
    return inbox.default_counterpart, None, None


def _file_kind(content_type, name):
    ct = (content_type or "").lower()
    if ct.startswith("image/") or ct.startswith("video/"):
        return RecordFile.Kind.PHOTO
    if ct == "application/pdf" or name.lower().endswith(".pdf"):
        return RecordFile.Kind.LETTER
    return RecordFile.Kind.OTHER


def file_email(parsed):
    """Save one email. Returns (correspondence, created) or (None, False) when no inbox matches."""
    inbox = find_inbox(parsed.recipients)
    if inbox is None:
        return None, False
    ws = inbox.workspace
    key = (
        parsed.message_id
        or hashlib.sha256(f"{parsed.sender}|{parsed.subject}|{parsed.date}|{parsed.text[:500]}".encode()).hexdigest()
    )
    existing = Correspondence.objects.filter(workspace=ws, external_message_id=key).first()
    if existing:
        return existing, False
    sender_name, sender = parsed.sender_name, parsed.sender
    forwarded = _is_staff_sender(ws, sender)
    if forwarded:
        fwd_name, fwd_addr = original_sender(parsed.text)
        if fwd_addr:
            sender_name, sender = fwd_name, fwd_addr
    counterpart, landlord, person = classify(ws, sender, inbox)
    subject = re.sub(r"^\s*((fwd?|fw|re)\s*:\s*)+", "", parsed.subject or "", flags=re.IGNORECASE) or "(no subject)"
    try:
        with transaction.atomic():
            c = Correspondence.objects.create(
                workspace=ws,
                counterpart=counterpart,
                landlord=landlord,
                person=person,
                property=inbox.property,
                counterpart_name=(f"{sender_name} <{sender}>" if sender_name else sender)[:200],
                channel=Correspondence.Channel.EMAIL,
                direction=Correspondence.Direction.IN,
                occurred_at=parsed.date,
                subject=subject[:255],
                body=(parsed.text or "")[:MAX_BODY],
                status=Correspondence.Status.NEEDS_REPLY,
                external_message_id=key,
                external_thread_id=f"email:{parsed.in_reply_to or key}"[:120],
            )
            ct = ContentType.objects.get_for_model(Correspondence)
            for name, content_type, data in parsed.attachments:
                RecordFile.objects.create(
                    workspace=ws,
                    content_type=ct,
                    object_id=c.pk,
                    kind=_file_kind(content_type, name),
                    file=ContentFile(data, name=name[:120] or "attachment"),
                    title=name[:200],
                )
    except IntegrityError:  # the same message arrived twice at the same moment
        return Correspondence.objects.get(workspace=ws, external_message_id=key), False
    SyncLog.objects.create(
        workspace=ws,
        direction="in",
        kind="email",
        idempotency_key=key[:200],
        message=f"{inbox.address}: {subject[:120]} ({len(parsed.attachments)} attachments)",
    )
    return c, True
