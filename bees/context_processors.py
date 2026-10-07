from .translations import TRANSLATIONS, LANGUAGE_NAMES


def cart_count(request):
    """Counts only items the cart page will actually show (products that
    still exist and are live), so the badge and the cart agree."""
    cart = request.session.get("cart", {})
    ids = [int(pid) for pid in cart if str(pid).isdigit()]
    if not ids:
        return {"cart_count": 0}
    from .models import Product
    live = set(Product.objects.live().filter(id__in=ids).values_list("id", flat=True))
    total = 0
    for pid, qty in cart.items():
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            continue
        if str(pid).isdigit() and int(pid) in live and qty > 0:
            total += qty
    return {"cart_count": total}


def wishlist_ids(request):
    if request.user.is_authenticated:
        from .models import Wishlist
        return {"wishlist_ids": set(Wishlist.objects.filter(user=request.user).values_list("product_id", flat=True))}
    return {"wishlist_ids": set()}


def _site_settings(request):
    """Loads SiteSettings once per request (several processors need it)."""
    if not hasattr(request, "_site_settings_cache"):
        from .models import SiteSettings
        try:
            request._site_settings_cache = SiteSettings.load()
        except Exception:
            request._site_settings_cache = SiteSettings()
    return request._site_settings_cache


def site_language(request):
    brand_obj = _site_settings(request)
    lang = request.session.get("site_lang", "en") if brand_obj.show_language_menu else "en"
    if lang not in TRANSLATIONS:
        lang = "en"
    store = brand_obj.site_name
    strings = {key: value.replace("{store}", store) for key, value in TRANSLATIONS[lang].items()}
    if lang != "en":
        # Fall back to English for any key a translation is missing.
        strings = {**{k: v.replace("{store}", store) for k, v in TRANSLATIONS["en"].items()}, **strings}
    return {
        "t": strings,
        "current_lang": lang,
        "language_names": LANGUAGE_NAMES,
    }


def trending_searches(request):
    """Kept for template compatibility; evaluated only if a template uses it."""
    from django.utils.functional import SimpleLazyObject
    from .models import SearchLog
    return {"trending_searches": SimpleLazyObject(lambda: list(SearchLog.objects.all()[:5]))}


def roles(request):
    """Cheap role flags for the header: is the user a seller, and (for
    staff) how many things in the store admin need attention."""
    user = request.user
    if not user.is_authenticated:
        return {"is_seller": False, "admin_attention": 0}
    from django.core.cache import cache
    from .models import SellerAccount, OrganizationMember
    is_seller = cache.get(f"is_seller:{user.pk}")
    if is_seller is None:
        is_seller = (
            SellerAccount.objects.filter(user=user).exists()
            or OrganizationMember.objects.filter(user=user).exists()
        )
        cache.set(f"is_seller:{user.pk}", is_seller, 300)
    attention = 0
    if user.is_staff:
        from .manage_views import attention_counts
        attention = attention_counts()["total"]
    return {"is_seller": is_seller, "admin_attention": attention}


def unread_notifications(request):
    if request.user.is_authenticated:
        from .models import Notification
        return {"unread_notifications_count": Notification.objects.filter(user=request.user, is_read=False).count()}
    return {"unread_notifications_count": 0}


def compare_count(request):
    return {"compare_count": len(request.session.get("compare", [])), "compare_ids": request.session.get("compare", [])}


def site_banner(request):
    settings_obj = _site_settings(request)
    if settings_obj.banner_active and settings_obj.banner_text:
        return {"site_banner": settings_obj}
    return {"site_banner": None}


def brand(request):
    """Exposes the white-label brand settings (store name, logo, colours,
    contact details) to every template as {{ brand.* }}."""
    from django.conf import settings as dj_settings
    from . import payments
    from .models import Product
    brand_obj = _site_settings(request)
    return {
        "brand": brand_obj,
        "stripe_enabled": payments.is_configured(),
        "store_currency": dj_settings.STORE_CURRENCY.upper(),
        "nav_categories": Product.CATEGORY_CHOICES,
    }
