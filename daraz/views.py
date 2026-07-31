from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from .models import Product, Review, Order, OrderItem

CATEGORY_IMAGE_IDS = {
    "skincare": 26, "haircare": 27, "grocery": 30, "fashion": 31, "electronics": 48,
    "3d-printers": 60, "pasta-tools": 61, "sim-devices": 62, "screen-protector": 63,
    "casserole-pot": 64, "table-lamp": 65, "hoodies": 66, "toy-boxes": 67,
    "sneakers": 68, "education": 69, "dress-up-kits": 70, "microphones": 71,
    "leashes": 72, "donate-education": 73, "coloring-drawing": 74, "lotion-cream": 75,
}


def home(request):
    flash_sale_products = Product.objects.filter(is_flash_sale=True)[:8]
    just_for_you_products = Product.objects.all().order_by("-created_at")[:16]
    categories = [
        {
            "slug": slug,
            "label": label,
            "image": f"https://picsum.photos/id/{CATEGORY_IMAGE_IDS.get(slug, 10)}/200/150",
        }
        for slug, label in Product.CATEGORY_CHOICES
    ]
    return render(request, "daraz/index.html", {
        "flash_sale_products": flash_sale_products,
        "just_for_you_products": just_for_you_products,
        "categories": categories,
    })


def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)

    if request.method == "POST":
        Review.objects.create(
            product=product,
            username=request.POST.get("username") or "Anonymous",
            rating=int(request.POST.get("rating", 5)),
            comment=request.POST.get("comment", ""),
        )
        return redirect("product_detail", pk=pk)

    reviews = product.reviews.all()
    return render(request, "daraz/product_detail.html", {
        "product": product,
        "reviews": reviews,
    })


def category_products(request, category):
    products = Product.objects.filter(category=category)
    label = dict(Product.CATEGORY_CHOICES).get(category, category)
    return render(request, "daraz/category.html", {
        "products": products,
        "category": category,
        "category_label": label,
    })


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        confirm = request.POST.get("confirm_password", "")

        if not username or not password:
            messages.error(request, "Username and password are required.")
        elif password != confirm:
            messages.error(request, "Passwords do not match.")
        elif User.objects.filter(username=username).exists():
            messages.error(request, "That username is already taken.")
        else:
            user = User.objects.create_user(username=username, email=email, password=password)
            auth_login(request, user)
            messages.success(request, "Account created. Welcome to Copy-Daraz.")
            return redirect("home")

    return render(request, "daraz/signup.html", {
        "google_client_id": settings.GOOGLE_CLIENT_ID,
    })


def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            auth_login(request, user)
            return redirect("home")
        messages.error(request, "Incorrect username or password.")

    return render(request, "daraz/login.html", {
        "google_client_id": settings.GOOGLE_CLIENT_ID,
    })


def logout_view(request):
    auth_logout(request)
    return redirect("home")


@csrf_exempt
def google_auth(request):
    """
    Receives the Google Identity Services credential (ID token) from the
    frontend, verifies it with Google, and logs the user in (creating an
    account on first sign-in). Requires GOOGLE_CLIENT_ID to be configured.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    if not settings.GOOGLE_CLIENT_ID:
        return JsonResponse({"error": "Google sign-in is not configured on this server."}, status=400)

    token = request.POST.get("credential")
    if not token:
        return JsonResponse({"error": "Missing credential"}, status=400)

    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests

        idinfo = id_token.verify_oauth2_token(
            token, google_requests.Request(), settings.GOOGLE_CLIENT_ID
        )
    except Exception as exc:
        return JsonResponse({"error": f"Invalid Google token: {exc}"}, status=400)

    email = idinfo.get("email")
    name = idinfo.get("name", email.split("@")[0] if email else "google_user")

    if not email:
        return JsonResponse({"error": "Google account has no email"}, status=400)

    user, created = User.objects.get_or_create(
        username=email,
        defaults={"email": email, "first_name": name},
    )
    auth_login(request, user)
    return JsonResponse({"success": True, "redirect": "/"})


def _get_cart_items(request):
    """Reads the session cart {product_id: qty} and returns (items, total, count)."""
    cart = request.session.get("cart", {})
    items = []
    total = 0
    count = 0
    for pid, qty in cart.items():
        try:
            product = Product.objects.get(pk=pid)
        except Product.DoesNotExist:
            continue
        subtotal = product.price * qty
        total += subtotal
        count += qty
        items.append({"product": product, "qty": qty, "subtotal": subtotal})
    return items, total, count


def cart_count(request):
    cart = request.session.get("cart", {})
    return sum(cart.values())


def add_to_cart(request, pk):
    product = get_object_or_404(Product, pk=pk)
    cart = request.session.get("cart", {})
    key = str(pk)
    qty = int(request.POST.get("quantity", 1)) if request.method == "POST" else 1
    cart[key] = cart.get(key, 0) + qty
    request.session["cart"] = cart
    request.session.modified = True
    messages.success(request, f"{product.name} added to cart.")
    next_url = request.POST.get("next") or request.GET.get("next") or "cart"
    return redirect(next_url)


def update_cart_item(request, pk):
    cart = request.session.get("cart", {})
    key = str(pk)
    action = request.POST.get("action")
    if key in cart:
        if action == "increase":
            cart[key] += 1
        elif action == "decrease":
            cart[key] -= 1
            if cart[key] <= 0:
                del cart[key]
        elif action == "remove":
            del cart[key]
    request.session["cart"] = cart
    request.session.modified = True
    return redirect("cart")


def cart_view(request):
    items, total, count = _get_cart_items(request)
    return render(request, "daraz/cart.html", {
        "items": items,
        "total": total,
        "count": count,
    })


@login_required
def checkout_view(request):
    items, total, count = _get_cart_items(request)
    if not items:
        messages.error(request, "Your cart is empty.")
        return redirect("cart")

    if request.method == "POST":
        order = Order.objects.create(
            user=request.user,
            full_name=request.POST.get("full_name", request.user.username),
            address=request.POST.get("address", ""),
            city=request.POST.get("city", ""),
            phone=request.POST.get("phone", ""),
            payment_method=request.POST.get("payment_method", "cod"),
        )
        for item in items:
            OrderItem.objects.create(
                order=order,
                product=item["product"],
                product_name=item["product"].name,
                price=item["product"].price,
                quantity=item["qty"],
            )
        request.session["cart"] = {}
        request.session.modified = True
        return redirect("order_success", order_id=order.id)

    return render(request, "daraz/checkout.html", {
        "items": items,
        "total": total,
    })


@login_required
def order_success(request, order_id):
    order = get_object_or_404(Order, pk=order_id, user=request.user)
    return render(request, "daraz/order_success.html", {"order": order})


@login_required
def my_orders(request):
    orders = Order.objects.filter(user=request.user).order_by("-created_at")
    return render(request, "daraz/my_orders.html", {"orders": orders})


def set_language(request, lang_code):
    from .translations import TRANSLATIONS
    if lang_code in TRANSLATIONS:
        request.session["site_lang"] = lang_code
    next_url = request.META.get("HTTP_REFERER", "/")
    return redirect(next_url)


def help_support(request):
    faqs = [
        ("How do I place an order?", "Add products to your cart, go to Cart, click 'Proceed to Checkout', fill in your delivery details and confirm. You must be logged in to check out."),
        ("What payment methods are available?", "Cash on delivery, credit/debit card, and Copy-Daraz wallet (demo options — no real payment is processed on this practice site)."),
        ("How can I track my order?", "Go to 'My orders' from the top menu after logging in to see all your past orders and their status."),
        ("Can I return a product?", "This is a learning project, so returns aren't processed automatically, but in a real store you'd typically get 7-14 days to request a return from your order page."),
        ("How do I change the site language?", "Use the 'Change language' option in the top bar to switch between English, Urdu, and Roman Urdu."),
        ("I forgot my password, what do I do?", "This demo site doesn't yet have a password-reset flow. Please sign up with a new username for now."),
    ]
    return render(request, "daraz/help.html", {"faqs": faqs})


def sell_on_daraz(request):
    return render(request, "daraz/static_page.html", {
        "page_title": "Sell on Copy-Daraz",
        "sections": [
            ("Reach more buyers", "Register your shop and list your products in front of shoppers browsing every category on Copy-Daraz — from electronics to fashion to groceries."),
            ("Simple onboarding", "Sign up with your account, add your product catalog, and start receiving orders. Sellers can track orders through the same dashboard used for buying."),
            ("Note", "This is a demo storefront built for learning purposes. Seller registration isn't wired to a real payout system — it's here to show how the page would work on a full marketplace."),
        ],
    })


def about_us(request):
    return render(request, "daraz/static_page.html", {
        "page_title": "About Copy-Daraz",
        "sections": [
            ("What this is", "Copy-Daraz is a practice e-commerce project built to learn how modern online marketplaces work end to end — browsing, cart, checkout, and order tracking."),
            ("Not a real store", "It is not affiliated with any real marketplace. It's a personal Django learning project styled after popular shopping sites, with working accounts, cart totals, and order history."),
        ],
    })


def terms_page(request):
    return render(request, "daraz/static_page.html", {
        "page_title": "Terms and Conditions",
        "sections": [
            ("Practice project", "This site is a learning demo. No real payments are processed and no goods are actually shipped."),
            ("Accounts", "Any account you create here is only used to demonstrate login, cart, and order features."),
        ],
    })


def privacy_page(request):
    return render(request, "daraz/static_page.html", {
        "page_title": "Privacy Policy",
        "sections": [
            ("What we store", "Only what's needed to demo the site: your username, email (if provided), cart contents, and any orders you place."),
            ("No sharing", "Nothing you enter here is shared with third parties — this is a local/personal practice project, not a live commercial service."),
        ],
    })
