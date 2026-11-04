"""Ready-to-post text for a listing, in the workspace's language.

Used for the Telegram post and as copy-and-paste text for channels with no API
(Facebook groups and Marketplace, Kijiji, Craigslist). The street address is
never included; only city and region.
"""

from django.utils import formats, translation
from django.utils.translation import gettext as _


def _money(amount, currency):
    return f"{formats.number_format(amount, 0, use_l10n=True, force_grouping=True)} {currency}"


def build_post(listing, channel=None):
    space = listing.space
    prop = space.property
    currency = space.get_currency()
    with translation.override(listing.workspace.language or "en"):
        lines = [listing.title]
        if listing.summary:
            lines.append(listing.summary)
        lines.append("")
        where = ", ".join(x for x in [prop.city, prop.region] if x)
        lines.append(f"📍 {where}")
        monthly, nightly = listing.price_monthly(), listing.price_nightly()
        if listing.stay_type in ("long", "both") and monthly:
            lines.append("💵 " + _("%(price)s per month") % {"price": _money(monthly, currency)})
        if listing.stay_type in ("short", "both") and nightly:
            lines.append("🌙 " + _("%(price)s per night") % {"price": _money(nightly, currency)})
        if space.deposit:
            lines.append(_("Deposit: %(price)s") % {"price": _money(space.deposit, currency)})
        if listing.available_from:
            lines.append("📅 " + _("Available from %(date)s") % {"date": formats.date_format(listing.available_from)})
        facts = [space.get_kind_display()]
        if space.bathroom:
            facts.append(space.get_bathroom_display())
        if space.furnished:
            facts.append(_("Furnished"))
        if space.max_occupants:
            facts.append(_("Up to %(n)s people") % {"n": space.max_occupants})
        lines.append("🏠 " + " · ".join(str(f) for f in facts))
        if listing.description:
            lines += ["", listing.description.strip()]
        amenities = [a.strip() for a in listing.amenities.splitlines() if a.strip()]
        if amenities:
            lines += ["", _("Included:")] + [f"• {a}" for a in amenities]
        if listing.contact_line:
            lines += ["", "📩 " + listing.contact_line]
    return "\n".join(lines).strip()
