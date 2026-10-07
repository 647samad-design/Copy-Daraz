"""Shipping fee lookup by destination country."""
from decimal import Decimal

from django.core.cache import cache

CACHE_KEY = "shipping_zones:v1"


def _zones():
    zones = cache.get(CACHE_KEY)
    if zones is None:
        from .models import ShippingZone
        zones = list(ShippingZone.objects.filter(active=True))
        cache.set(CACHE_KEY, zones, 60)
    return zones


def clear_cache():
    cache.delete(CACHE_KEY)


def zone_for(country):
    """The zone that ships to ``country``, or None. A zone listing the
    country wins over a rest-of-world (*) zone."""
    country = (country or "").upper()
    rest = None
    for zone in _zones():
        if zone.is_rest_of_world:
            rest = rest or zone
        elif country and country in zone.country_codes:
            return zone
    return rest


def quote(country, amount):
    """Shipping for an order to ``country`` whose goods cost ``amount``
    (after discounts). Returns {"ships", "fee", "zone"}. ``fee`` is None
    when the country isn't known yet."""
    from .models import SiteSettings
    zones = _zones()
    if not zones:
        brand = SiteSettings.load()
        return {"ships": True, "fee": brand.shipping_for(amount), "zone": None}
    if not country:
        return {"ships": True, "fee": None, "zone": None}
    zone = zone_for(country)
    if not zone:
        return {"ships": False, "fee": None, "zone": None}
    return {"ships": True, "fee": zone.fee_for(amount), "zone": zone}


def table():
    """Data for the checkout page to show shipping as soon as a country is
    picked (the server recalculates on submit)."""
    from .models import SiteSettings
    zones = _zones()
    if not zones:
        brand = SiteSettings.load()
        return {"flat": {"fee": str(brand.shipping_flat_fee or 0),
                         "free_over": str(brand.free_shipping_threshold) if brand.free_shipping_threshold else None}}
    data = {"countries": {}, "rest": None}
    for zone in zones:
        entry = {"fee": str(zone.fee), "free_over": str(zone.free_over) if zone.free_over is not None else None, "name": zone.name}
        if zone.is_rest_of_world:
            data["rest"] = data["rest"] or entry
        else:
            for code in zone.country_codes:
                data["countries"].setdefault(code, entry)
    return data


def delivery_days(country):
    zone = zone_for(country) if _zones() else None
    return zone.delivery_days if zone and zone.delivery_days else None


ZERO = Decimal("0")
