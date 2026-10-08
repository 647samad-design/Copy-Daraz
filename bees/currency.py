"""Show prices in the shopper's currency.

Shoppers pick a currency in the header. Product prices are converted with
the rates in Admin > Currencies and shown with "≈" where it matters;
the cart, checkout, invoices and payments always use the store currency
(STORE_CURRENCY), so nobody is ever charged a converted amount.
"""
import contextvars
import json
import logging
import urllib.request
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_KEY = "currencies:v1"
SESSION_KEY = "currency"
RATES_URL = "https://open.er-api.com/v6/latest/{base}"
# Back-office pages always work in the store currency. On the shop, product
# prices use the |price filter (converted) while cart, checkout, orders and
# emails use |money (store currency) with an optional |approx hint.
STORE_CURRENCY_PATHS = ("/manage/", "/seller/", "/admin/")

_display = contextvars.ContextVar("display_currency", default=None)


def store_code():
    return getattr(settings, "STORE_CURRENCY", "usd").upper()


def active_currencies():
    data = cache.get(CACHE_KEY)
    if data is None:
        from .models import Currency
        data = list(Currency.objects.filter(active=True).exclude(code=store_code()))
        cache.set(CACHE_KEY, data, 300)
    return data


def get(code):
    code = (code or "").upper()
    return next((c for c in active_currencies() if c.code == code), None)


def current():
    """Currency prices are being shown in for this request (None = store currency)."""
    return _display.get()


def convert(amount, cur):
    value = Decimal(str(amount or 0)) * cur.rate
    step = Decimal("1") if cur.decimals == 0 else Decimal("0.01")
    return value.quantize(step, rounding=ROUND_HALF_UP)


def fmt(amount, cur):
    value = convert(amount, cur)
    return f"{cur.symbol}{value:,.0f}" if cur.decimals == 0 else f"{cur.symbol}{value:,.2f}"


class DisplayCurrencyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        cur = None
        code = request.session.get(SESSION_KEY) if hasattr(request, "session") else None
        if code and not request.path.startswith(STORE_CURRENCY_PATHS):
            cur = get(code)
        token = _display.set(cur)
        try:
            return self.get_response(request)
        finally:
            _display.reset(token)


def update_rates():
    """Downloads today's rates for every currency. Returns (updated, error)."""
    from .models import Currency
    base = store_code()
    try:
        with urllib.request.urlopen(RATES_URL.format(base=base), timeout=15) as resp:
            data = json.loads(resp.read().decode())
        rates = data.get("rates") or {}
        if data.get("result") not in (None, "success") or not rates:
            raise ValueError(data.get("error-type") or "no rates in the reply")
    except Exception as exc:  # network blocked, service down, bad reply
        logger.warning("Currency rates update failed: %s", exc)
        return 0, f"Couldn't download rates ({exc}). You can type them in by hand."
    updated = 0
    now = timezone.now()
    for cur in Currency.objects.all():
        rate = rates.get(cur.code)
        if rate:
            cur.rate = Decimal(str(rate)).quantize(Decimal("0.000001"))
            cur.updated_at = now
            cur.save()
            updated += 1
    cache.delete(CACHE_KEY)
    return updated, None
