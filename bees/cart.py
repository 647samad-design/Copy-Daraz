"""Session cart helpers.

The cart is stored in the session as {key: quantity}. A key is the product
id ("12"), or product and variant id for sizes/colours ("12-34").
"""
from decimal import Decimal


def make_key(product_id, variant_id=None):
    return f"{product_id}-{variant_id}" if variant_id else str(product_id)


def parse_key(key):
    """Returns (product_id, variant_id or None), or None for junk."""
    parts = str(key).split("-")
    if len(parts) == 1 and parts[0].isdigit():
        return int(parts[0]), None
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        return int(parts[0]), int(parts[1])
    return None


def lines(cart):
    """Turns a cart dict into rows for live products:
    {key, product, variant, qty, unit_price, subtotal, label, stock}.
    Missing/hidden products, removed variants and bad quantities are
    skipped."""
    from .models import Product, ProductVariant

    parsed = {}
    for key, qty in (cart or {}).items():
        ids = parse_key(key)
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            continue
        if ids and qty > 0:
            parsed[key] = (ids, qty)
    if not parsed:
        return []
    products = Product.objects.live().select_related("seller_account").in_bulk(
        {ids[0] for ids, _ in parsed.values()})
    variant_ids = {ids[1] for ids, _ in parsed.values() if ids[1]}
    variants = ProductVariant.objects.in_bulk(variant_ids) if variant_ids else {}
    rows = []
    for key, ((pid, vid), qty) in parsed.items():
        product = products.get(pid)
        if not product:
            continue
        variant = None
        if vid:
            variant = variants.get(vid)
            if not variant or variant.product_id != pid:
                continue
            variant.product = product
        elif product.has_variants:
            continue  # sizes were added after this went in the cart
        unit = variant.unit_price if variant else product.price
        rows.append({
            "key": key, "product": product, "variant": variant, "qty": qty,
            "unit_price": unit, "subtotal": unit * qty,
            "label": variant.label if variant else "",
            "stock": variant.stock if variant else product.stock,
        })
    return rows


def totals(rows):
    total = sum((r["subtotal"] for r in rows), Decimal("0"))
    count = sum(r["qty"] for r in rows)
    return total, count


def persist(request, user=None):
    """Saves the session cart on the signed-in customer's account."""
    from django.utils import timezone
    from .models import Profile
    user = user or request.user
    if not user or not user.is_authenticated:
        return
    cart = request.session.get("cart", {}) or {}
    profile = Profile.objects.filter(user=user).only("saved_cart").first()
    if profile is None:
        if not cart:
            return
        profile, _ = Profile.objects.get_or_create(user=user, defaults={"referral_code": _code()})
    if profile.saved_cart == cart:
        return
    Profile.objects.filter(pk=profile.pk).update(
        saved_cart=cart, cart_updated_at=timezone.now() if cart else None, cart_reminder_sent=False,
    )


def _code():
    import secrets
    return secrets.token_hex(4).upper()
