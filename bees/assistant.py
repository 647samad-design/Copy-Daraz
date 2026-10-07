"""Guided help in the support chat.

Customers pick a topic (or type a question) and get an instant, accurate
answer built from the store's real settings - payment methods, shipping
zones, return window - plus their own latest orders. Anything the
assistant can't answer goes to the store team, who reply from
Admin > Messages.
"""
from django.urls import reverse

MENU = ["order_help", "track", "shipping", "payment", "returns", "cancel", "account", "human"]

LABELS = {
    "order_help": "How do I order?",
    "track": "Where's my order?",
    "shipping": "Shipping & delivery",
    "payment": "Payment options",
    "returns": "Returns & refunds",
    "cancel": "Cancel an order",
    "account": "Account & password",
    "sell": "Sell on this store",
    "coupons": "Discount codes",
    "human": "Talk to a person",
    "menu": "Main menu",
}

KEYWORDS = [
    ("cancel", ["cancel", "cancellation"]),
    ("returns", ["return", "refund", "money back", "exchange", "damaged", "broken", "wrong item"]),
    ("track", ["track", "where is", "where's", "status", "not arrived", "late", "my order", "order number", "parcel", "package"]),
    ("shipping", ["shipping", "delivery", "deliver", "ship to", "courier", "how long", "country", "countries"]),
    ("payment", ["pay", "card", "visa", "mastercard", "cash", "cod", "stripe", "apple pay", "google pay", "charged", "payment"]),
    ("coupons", ["coupon", "discount", "promo", "voucher", "code"]),
    ("account", ["password", "login", "log in", "sign in", "account", "email", "verify", "2fa", "two-step"]),
    ("sell", ["sell", "seller", "vendor", "store owner", "commission", "list my"]),
    ("order_help", ["how to order", "how do i order", "how to buy", "buy", "purchase", "checkout", "place an order"]),
    ("human", ["human", "person", "agent", "someone", "talk to", "speak", "call me", "complaint"]),
]


def _brand():
    from .models import SiteSettings
    return SiteSettings.load()


def _money(value):
    from .templatetags.bees_extras import money
    return money(value)


def _link(label, url):
    return {"label": label, "url": url}


def _payment_methods():
    from . import payments
    methods = []
    if payments.is_configured():
        methods.append("card (Visa, Mastercard, Amex, Apple Pay and Google Pay through Stripe's secure page)")
    if _brand().allow_cash_on_delivery:
        methods.append("cash on delivery")
    return methods


def _shipping_text():
    from . import shipping
    table = shipping.table()
    brand = _brand()
    if "flat" in table:
        fee = brand.shipping_flat_fee or 0
        text = f"Shipping is {_money(fee)} per order" if fee else "Shipping is free"
        if brand.free_shipping_threshold:
            text += f", and free on orders over {_money(brand.free_shipping_threshold)}"
        return text + ". We deliver worldwide."
    lines = []
    from .models import ShippingZone
    for zone in ShippingZone.objects.filter(active=True):
        fee = _money(zone.fee) if zone.fee else "free"
        extra = f", free over {_money(zone.free_over)}" if zone.free_over is not None else ""
        days = f" · about {zone.delivery_days} business days" if zone.delivery_days else ""
        lines.append(f"• {zone.name}: {fee}{extra}{days}")
    text = "Shipping depends on where we deliver:\n" + "\n".join(lines)
    if not table.get("rest"):
        text += "\nWe only deliver to the countries in these zones for now."
    return text + "\nThe exact fee is shown at checkout as soon as you pick your country."


def answer(key, request):
    """Returns {"message", "links", "options"} for a topic."""
    brand = _brand()
    user = request.user if request.user.is_authenticated else None
    name = (user.first_name or user.username) if user else ""
    store = brand.site_name
    links, options = [], []

    if key == "menu" or key == "start":
        hello = f"Hi {name}! " if name else "Hi there! "
        message = (f"{hello}I'm the {store} help assistant. Pick a topic below and I'll answer right away, "
                   "or type your question. If you need a person, our team replies here, usually within a few hours.")
        options = MENU

    elif key == "order_help":
        message = ("Ordering takes about a minute:\n"
                   "1. Open a product and choose Add to cart (pick a size or colour first if it has options).\n"
                   "2. Open your cart and press Checkout.\n"
                   "3. Enter your delivery address. The shipping fee appears when you choose your country.\n"
                   f"4. Choose how to pay: {', or '.join(_payment_methods()) or 'the options shown'}.\n"
                   "5. You'll get an email confirmation with your order number and a tracking link.\n"
                   "You don't need an account: you can check out as a guest.")
        links = [_link("Browse products", reverse("all_products")), _link("Open my cart", reverse("cart"))]
        options = ["payment", "shipping", "coupons", "menu"]

    elif key == "track":
        if user:
            from .models import Order
            orders = list(Order.objects.filter(user=user).order_by("-created_at")[:3])
            if orders:
                rows = []
                for o in orders:
                    line = f"• Order #{o.id}: {o.get_status_display()}"
                    if o.status not in ("delivered", "cancelled") and o.estimated_delivery:
                        line += f", arriving by {o.estimated_delivery:%b %d}"
                    if o.tracking_number:
                        line += f" (tracking {o.tracking_number}{' with ' + o.courier_name if o.courier_name else ''})"
                    rows.append(line)
                message = "Here are your latest orders:\n" + "\n".join(rows) + "\nWe email you each time an order is confirmed, shipped and delivered."
            else:
                message = "You don't have any orders on this account yet. If you checked out as a guest, use the tracking link in your order email."
            links = [_link("Open My orders", reverse("my_orders"))]
        else:
            message = ("To see your order: open the tracking link in your order confirmation email, "
                       "or sign in and open My orders. Each email also shows the expected delivery date.")
            links = [_link("Sign in", reverse("login") + "?next=" + reverse("my_orders"))]
        options = ["shipping", "cancel", "human", "menu"]

    elif key == "shipping":
        message = _shipping_text() + (f"\nYou'll get an estimated delivery date at checkout and in your confirmation email.")
        options = ["track", "order_help", "menu"]

    elif key == "payment":
        methods = _payment_methods()
        message = "You can pay by " + (" or ".join(methods) if methods else "the options shown at checkout") + "."
        from . import payments
        if payments.is_configured():
            message += ("\nCard payments happen on Stripe's secure page; we never see your card number. "
                        "Signed-in customers can save a card for next time.")
            if brand.allow_cash_on_delivery:
                message += "\nChose cash on delivery but want to pay now? Open My orders and press Pay online now."
        message += "\nStore credit and reward points can be used at checkout when you're signed in."
        options = ["order_help", "coupons", "menu"]

    elif key == "returns":
        message = (f"You can return items within {brand.return_days} days of delivery.\n"
                   "1. Open My orders and choose Request a return next to the item.\n"
                   "2. Tell us what's wrong and choose your refund: back to your card, or store credit.\n"
                   "3. We review requests within 1-2 business days and email you.\n"
                   "Card refunds usually show within 5-10 business days. Cash on delivery orders are paid back directly or as store credit.")
        links = [_link("Open My orders", reverse("my_orders"))]
        options = ["cancel", "human", "menu"]

    elif key == "cancel":
        message = ("You can cancel an order yourself until it ships: open My orders (or the tracking link in your email) and press Cancel order.\n"
                   "If you already paid by card, the full amount goes back to your card automatically and we email you a confirmation. "
                   "Already shipped? Wait for delivery and request a return instead.")
        links = [_link("Open My orders", reverse("my_orders"))]
        options = ["returns", "human", "menu"]

    elif key == "coupons":
        message = ("Have a discount code? Enter it in the Coupon code box on the checkout page and press Apply. "
                   "Each code has its own rules (minimum order, expiry, one use per customer). "
                   "Invite friends from your profile: when they make their first purchase you get a 10% code.")
        links = [_link("Go to checkout", reverse("checkout"))]
        options = ["payment", "menu"]

    elif key == "account":
        message = ("• Forgot your password? Use Forgot password on the sign-in page and we'll email a reset link.\n"
                   "• Change your password, turn on two-step sign-in, or manage saved cards under Account.\n"
                   "• Didn't get the verification email? Check spam, or send a new code from your profile.")
        links = [_link("Reset password", reverse("password_reset")), _link("My account", reverse("profile"))]
        options = ["human", "menu"]

    elif key == "sell":
        message = (f"You can sell on {store} as an individual or a business. Apply, we review your details, "
                   "then you list products from your seller dashboard. We take a small commission on each sale; "
                   "the rest is paid out to you.")
        links = [_link("Start selling", reverse("sell_on_bees"))]
        options = ["human", "menu"]

    elif key == "human":
        message = ("Sure. Type your question below (include your order number if it's about an order) and a member of our team will reply right here"
                   + (", and we'll let you know by notification." if user else ". Keep this page open, or sign in so you don't miss the reply."))
        if brand.support_email:
            message += f"\nYou can also email us at {brand.support_email}."
            links = [_link("Email us", f"mailto:{brand.support_email}")]
        options = ["menu"]
    else:
        return answer("menu", request)

    return {"message": message, "links": links, "options": [{"key": k, "label": LABELS[k]} for k in options]}


def match(text):
    text = text.lower()
    for key, words in KEYWORDS:
        if any(w in text for w in words):
            return key
    return None
