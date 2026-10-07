from .translations import TRANSLATIONS, LANGUAGE_NAMES


def cart_count(request):
    cart = request.session.get("cart", {})
    return {"cart_count": sum(cart.values())}


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
    from .models import SearchLog
    return {"trending_searches": SearchLog.objects.all()[:5]}


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
