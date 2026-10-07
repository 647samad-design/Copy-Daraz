from decimal import Decimal, InvalidOperation

from django import template
from django.conf import settings

register = template.Library()

CURRENCY_SYMBOLS = {
    "usd": "$", "eur": "€", "gbp": "£", "cad": "CA$", "aud": "A$",
    "aed": "AED ", "sar": "SAR ", "inr": "₹", "pkr": "Rs ", "jpy": "¥",
}
ZERO_DECIMAL = {"jpy", "krw", "vnd"}


@register.filter
def get_item(dictionary, key):
    if not dictionary:
        return ""
    return dictionary.get(key, "")


@register.filter
def money(value):
    """Formats an amount in the store currency, e.g. 1234.5 -> $1,234.50."""
    currency = getattr(settings, "STORE_CURRENCY", "usd").lower()
    symbol = CURRENCY_SYMBOLS.get(currency, currency.upper() + " ")
    try:
        amount = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        return value
    if currency in ZERO_DECIMAL:
        return f"{symbol}{amount:,.0f}"
    return f"{symbol}{amount:,.2f}"


@register.simple_tag
def currency_code():
    return getattr(settings, "STORE_CURRENCY", "usd").upper()


@register.filter
def stars(value):
    """Rating (0-5) -> '★★★★☆' style string."""
    try:
        full = int(round(float(value or 0)))
    except (TypeError, ValueError):
        full = 0
    full = max(0, min(5, full))
    return "★" * full + "☆" * (5 - full)


@register.filter
def country_name(code):
    from ..countries import COUNTRY_NAMES
    return COUNTRY_NAMES.get((code or "").upper(), code)


@register.inclusion_tag("bees/partials/country_select.html")
def country_select(name="country", selected="", field_id="id_country", required=True):
    from ..countries import COUNTRIES
    return {"countries": COUNTRIES, "name": name, "selected": (selected or "").upper(), "field_id": field_id, "required": required}


@register.simple_tag
def site_brand():
    """SiteSettings for templates rendered without a request (emails)."""
    from ..models import SiteSettings
    try:
        return SiteSettings.load()
    except Exception:
        return SiteSettings()
