from .translations import TRANSLATIONS, LANGUAGE_NAMES


def cart_count(request):
    cart = request.session.get("cart", {})
    return {"cart_count": sum(cart.values())}


def site_language(request):
    lang = request.session.get("site_lang", "en")
    return {
        "t": TRANSLATIONS.get(lang, TRANSLATIONS["en"]),
        "current_lang": lang,
        "language_names": LANGUAGE_NAMES,
    }
