from .translations import TRANSLATIONS, LANGUAGE_NAMES


def cart_count(request):
    cart = request.session.get("cart", {})
    return {"cart_count": sum(cart.values())}


def wishlist_ids(request):
    if request.user.is_authenticated:
        from .models import Wishlist
        return {"wishlist_ids": set(Wishlist.objects.filter(user=request.user).values_list("product_id", flat=True))}
    return {"wishlist_ids": set()}


def site_language(request):
    lang = request.session.get("site_lang", "en")
    return {
        "t": TRANSLATIONS.get(lang, TRANSLATIONS["en"]),
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
