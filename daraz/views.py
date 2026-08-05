from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponse
from django.core.paginator import Paginator
from django.db.models import Q
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
import random
import string
from .models import (
    Product, Review, Order, OrderItem, Wishlist, Coupon,
    ProductImage, Profile, Address, Question, NewsletterSubscriber,
    Notification, SearchLog, SellerAccount,
)


def _apply_sort(qs, sort):
    if sort == "price_asc":
        return qs.order_by("price")
    if sort == "price_desc":
        return qs.order_by("-price")
    if sort == "newest":
        return qs.order_by("-created_at")
    if sort == "rating":
        return sorted(qs, key=lambda p: p.average_rating, reverse=True)
    return qs.order_by("-id")

CATEGORY_IMAGE_IDS = {
    "skincare": 26, "haircare": 27, "grocery": 30, "fashion": 31, "electronics": 48,
    "3d-printers": 60, "pasta-tools": 61, "sim-devices": 62, "screen-protector": 63,
    "casserole-pot": 64, "table-lamp": 65, "hoodies": 66, "toy-boxes": 67,
    "sneakers": 68, "education": 69, "dress-up-kits": 70, "microphones": 71,
    "leashes": 72, "donate-education": 73, "coloring-drawing": 74, "lotion-cream": 75,
}


def home(request):
    query = request.GET.get("q", "").strip()
    if query:
        return redirect(f"/search/?q={query}")
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
    in_wishlist = False
    if request.user.is_authenticated:
        in_wishlist = Wishlist.objects.filter(user=request.user, product=product).exists()

    gallery = [product.image_url] + list(product.extra_images.values_list("image_url", flat=True))
    related_products = Product.objects.filter(category=product.category).exclude(pk=product.pk)[:6]
    questions = product.questions.all()

    recent_ids = request.session.get("recently_viewed", [])
    recent_ids = [i for i in recent_ids if i != product.id]
    recent_ids.insert(0, product.id)
    request.session["recently_viewed"] = recent_ids[:10]
    request.session.modified = True
    recently_viewed = Product.objects.filter(id__in=recent_ids[1:7])

    return render(request, "daraz/product_detail.html", {
        "product": product,
        "reviews": reviews,
        "in_wishlist": in_wishlist,
        "gallery": gallery,
        "related_products": related_products,
        "questions": questions,
        "recently_viewed": recently_viewed,
    })


def category_products(request, category):
    products = Product.objects.filter(category=category)
    sort = request.GET.get("sort", "")
    products = _apply_sort(products, sort)
    paginator = Paginator(products, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    label = dict(Product.CATEGORY_CHOICES).get(category, category)
    return render(request, "daraz/category.html", {
        "products": page_obj,
        "page_obj": page_obj,
        "category": category,
        "category_label": label,
        "current_sort": sort,
    })


def search_products(request):
    query = request.GET.get("q", "").strip()
    if query:
        log, _ = SearchLog.objects.get_or_create(query__iexact=query, defaults={"query": query})
        SearchLog.objects.filter(pk=log.pk).update(count=log.count + 1)
    results = Product.objects.filter(name__icontains=query) if query else Product.objects.none()
    sort = request.GET.get("sort", "")
    results = _apply_sort(results, sort)
    paginator = Paginator(results, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "daraz/search.html", {
        "products": page_obj,
        "page_obj": page_obj,
        "query": query,
        "current_sort": sort,
    })


def all_products(request):
    products = Product.objects.all()
    sort = request.GET.get("sort", "")
    products = _apply_sort(products, sort)
    paginator = Paginator(products, 16)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "daraz/all_products.html", {
        "products": page_obj,
        "page_obj": page_obj,
        "current_sort": sort,
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
            code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
            ref = request.GET.get("ref") or request.POST.get("ref", "")
            profile = Profile.objects.create(user=user, referral_code=code, referred_by=ref)
            if ref:
                referrer_profile = Profile.objects.filter(referral_code=ref).first()
                if referrer_profile:
                    Notification.objects.create(
                        user=referrer_profile.user,
                        message=f"{username} joined using your referral link!",
                        link="/profile/",
                    )
            auth_login(request, user)
            if email:
                _send_verification_email(request, user)
            messages.success(request, "Account created. Welcome to 19Bees.")
            return redirect("home")

    return render(request, "daraz/signup.html", {
        "google_client_id": settings.GOOGLE_CLIENT_ID,
        "ref_code": request.GET.get("ref", ""),
    })


def _send_verification_email(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    scheme = "https" if request.is_secure() else "http"
    link = f"{scheme}://{request.get_host()}/verify-email/{uid}/{token}/"
    try:
        html_body = render_to_string("daraz/emails/verify_email.html", {"user": user, "link": link})
        email = EmailMultiAlternatives(
            "Verify your 19Bees email",
            strip_tags(html_body),
            None,
            [user.email],
        )
        email.attach_alternative(html_body, "text/html")
        email.send(fail_silently=True)
    except Exception:
        pass


def verify_email(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        profile, _ = Profile.objects.get_or_create(user=user)
        profile.email_verified = True
        profile.save()
        messages.success(request, "Your email has been verified.")
    else:
        messages.error(request, "This verification link is invalid or has expired.")
    return redirect("profile" if request.user.is_authenticated else "login")


@login_required
def resend_verification(request):
    _send_verification_email(request, request.user)
    messages.success(request, "Verification email sent.")
    return redirect("profile")


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
    if product.stock <= 0:
        messages.error(request, f"{product.name} is out of stock.")
        return redirect(request.POST.get("next") or request.GET.get("next") or "cart")
    cart = request.session.get("cart", {})
    key = str(pk)
    qty = int(request.POST.get("quantity", 1)) if request.method == "POST" else 1
    new_qty = cart.get(key, 0) + qty
    if new_qty > product.stock:
        new_qty = product.stock
        messages.warning(request, f"Only {product.stock} of {product.name} left in stock.")
    cart[key] = new_qty
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


def checkout_view(request):
    items, total, count = _get_cart_items(request)
    if not items:
        messages.error(request, "Your cart is empty.")
        return redirect("cart")

    coupon = None
    discount_amount = 0
    coupon_code = request.session.get("coupon_code", "")
    if coupon_code:
        coupon = Coupon.objects.filter(code__iexact=coupon_code, active=True).first()
        if coupon:
            discount_amount = round(total * coupon.percent_off / 100, 2)

    if request.method == "POST":
        for item in items:
            if item["qty"] > item["product"].stock:
                messages.error(request, f"Not enough stock for {item['product'].name}.")
                return redirect("cart")

        guest_email = request.POST.get("guest_email", "").strip()
        order = Order.objects.create(
            user=request.user if request.user.is_authenticated else None,
            guest_email=guest_email if not request.user.is_authenticated else "",
            full_name=request.POST.get("full_name", request.user.username if request.user.is_authenticated else "Guest"),
            address=request.POST.get("address", ""),
            city=request.POST.get("city", ""),
            phone=request.POST.get("phone", ""),
            payment_method=request.POST.get("payment_method", "cod"),
            coupon_code=coupon.code if coupon else "",
            discount_amount=discount_amount,
        )
        for item in items:
            OrderItem.objects.create(
                order=order,
                product=item["product"],
                product_name=item["product"].name,
                price=item["product"].price,
                quantity=item["qty"],
            )
            item["product"].stock = max(item["product"].stock - item["qty"], 0)
            item["product"].save(update_fields=["stock"])
        request.session["cart"] = {}
        request.session["coupon_code"] = ""
        request.session.modified = True

        recipient = order.user.email if order.user and order.user.email else order.guest_email
        if recipient:
            try:
                scheme = "https" if request.is_secure() else "http"
                invoice_url = f"{scheme}://{request.get_host()}/order/{order.id}/invoice/"
                html_body = render_to_string("daraz/emails/order_confirmation.html", {
                    "order": order,
                    "invoice_url": invoice_url,
                })
                email = EmailMultiAlternatives(
                    f"Your 19Bees order #{order.id} is confirmed",
                    strip_tags(html_body),
                    None,
                    [recipient],
                )
                email.attach_alternative(html_body, "text/html")
                email.send(fail_silently=True)
            except Exception:
                pass

        if order.user:
            Notification.objects.create(user=order.user, message=f"Order #{order.id} placed successfully.", link="/my-orders/")

        return redirect("order_success", order_id=order.id)

    return render(request, "daraz/checkout.html", {
        "items": items,
        "total": total,
        "discount_amount": discount_amount,
        "final_total": max(total - discount_amount, 0),
        "coupon": coupon,
        "addresses": Address.objects.filter(user=request.user) if request.user.is_authenticated else [],
    })


def apply_coupon(request):
    code = request.POST.get("coupon_code", "").strip()
    request.session["coupon_code"] = code
    request.session.modified = True
    if code and not Coupon.objects.filter(code__iexact=code, active=True).exists():
        messages.error(request, "Invalid or expired coupon code.")
    else:
        messages.success(request, "Coupon applied.")
    return redirect("checkout")


def order_success(request, order_id):
    if request.user.is_authenticated:
        order = get_object_or_404(Order, pk=order_id, user=request.user)
    else:
        order = get_object_or_404(Order, pk=order_id, user__isnull=True)
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
        ("What payment methods are available?", "Cash on delivery, credit/debit card, and 19Bees wallet (demo options — no real payment is processed on this practice site)."),
        ("How can I track my order?", "Go to 'My orders' from the top menu after logging in to see all your past orders and their status."),
        ("Can I return a product?", "This is a learning project, so returns aren't processed automatically, but in a real store you'd typically get 7-14 days to request a return from your order page."),
        ("How do I change the site language?", "Use the 'Change language' option in the top bar to switch between English, Urdu, and Roman Urdu."),
        ("I forgot my password, what do I do?", "This demo site doesn't yet have a password-reset flow. Please sign up with a new username for now."),
    ]
    return render(request, "daraz/help.html", {"faqs": faqs})


def sell_on_daraz(request):
    return render(request, "daraz/static_page.html", {
        "page_title": "Sell on 19Bees",
        "sections": [
            ("Reach more buyers", "Register your shop and list your products in front of shoppers browsing every category on 19Bees — from electronics to fashion to groceries."),
            ("Two account types", "Individual sellers pay a 10% platform commission. Organizations / registered businesses pay 20% and get a dedicated business profile."),
            ("Simple onboarding", "Apply below, get approved, and start listing products from your own seller dashboard — track sales, commission, and earnings in one place."),
        ],
        "cta_url": "/become-seller/",
        "cta_label": "Apply to become a seller",
    })


def about_us(request):
    return render(request, "daraz/static_page.html", {
        "page_title": "About 19Bees",
        "sections": [
            ("What this is", "19Bees is a practice e-commerce project built to learn how modern online marketplaces work end to end — browsing, cart, checkout, and order tracking."),
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


@login_required
def cancel_order(request, order_id):
    order = get_object_or_404(Order, pk=order_id, user=request.user)
    if order.status in ("pending", "confirmed"):
        order.status = "cancelled"
        order.save(update_fields=["status"])
        messages.success(request, f"Order #{order.id} has been cancelled.")
    else:
        messages.error(request, "This order can no longer be cancelled.")
    return redirect("my_orders")


@login_required
def toggle_wishlist(request, pk):
    product = get_object_or_404(Product, pk=pk)
    item, created = Wishlist.objects.get_or_create(user=request.user, product=product)
    if not created:
        item.delete()
        messages.info(request, f"Removed {product.name} from wishlist.")
    else:
        messages.success(request, f"Added {product.name} to wishlist.")
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "home"
    return redirect(next_url)


@login_required
def wishlist_view(request):
    items = Wishlist.objects.filter(user=request.user).select_related("product")
    return render(request, "daraz/wishlist.html", {"items": items})


@login_required
def profile_view(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        request.user.first_name = request.POST.get("first_name", "")
        request.user.email = request.POST.get("email", "")
        request.user.save()
        profile.phone = request.POST.get("phone", "")
        profile.save()
        messages.success(request, "Profile updated.")
        return redirect("profile")
    addresses = Address.objects.filter(user=request.user)
    orders_count = Order.objects.filter(user=request.user).count()
    return render(request, "daraz/profile.html", {
        "profile": profile,
        "addresses": addresses,
        "orders_count": orders_count,
    })


@login_required
def add_address(request):
    if request.method == "POST":
        Address.objects.create(
            user=request.user,
            label=request.POST.get("label", "Home"),
            full_name=request.POST.get("full_name", ""),
            phone=request.POST.get("phone", ""),
            address=request.POST.get("address", ""),
            city=request.POST.get("city", ""),
        )
        messages.success(request, "Address saved.")
    return redirect("profile")


@login_required
def delete_address(request, pk):
    Address.objects.filter(pk=pk, user=request.user).delete()
    return redirect("profile")


def store_page(request, seller_name):
    products = Product.objects.filter(seller_name=seller_name)
    return render(request, "daraz/store.html", {
        "seller_name": seller_name,
        "products": products,
    })


def newsletter_subscribe(request):
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        if email:
            NewsletterSubscriber.objects.get_or_create(email=email)
            messages.success(request, "Thanks for subscribing!")
    return redirect(request.META.get("HTTP_REFERER", "home"))


def ask_question(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        Question.objects.create(
            product=product,
            username=request.POST.get("username") or (request.user.username if request.user.is_authenticated else "Anonymous"),
            question=request.POST.get("question", ""),
        )
        messages.success(request, "Your question has been posted.")
    return redirect("product_detail", pk=pk)


def invoice_pdf(request, order_id):
    if request.user.is_authenticated:
        order = get_object_or_404(Order, pk=order_id, user=request.user)
    else:
        order = get_object_or_404(Order, pk=order_id, user__isnull=True)

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from io import BytesIO

    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    p.setFillColorRGB(0.97, 0.34, 0.02)
    p.rect(0, height - 60, width, 60, fill=1, stroke=0)
    p.setFillColorRGB(1, 1, 1)
    p.setFont("Helvetica-Bold", 20)
    p.drawString(40, height - 40, "19Bees")

    p.setFillColorRGB(0, 0, 0)
    p.setFont("Helvetica-Bold", 14)
    p.drawString(40, height - 90, f"Invoice - Order #{order.id}")
    p.setFont("Helvetica", 10)
    p.drawString(40, height - 110, f"Date: {order.created_at.strftime('%d %b %Y')}")
    p.drawString(40, height - 125, f"Status: {order.get_status_display()}")
    p.drawString(40, height - 145, f"Deliver to: {order.full_name}, {order.address}, {order.city}")
    p.drawString(40, height - 160, f"Phone: {order.phone}")

    y = height - 200
    p.setFont("Helvetica-Bold", 10)
    p.drawString(40, y, "Item")
    p.drawString(320, y, "Qty")
    p.drawString(370, y, "Price")
    p.drawString(450, y, "Subtotal")
    y -= 16
    p.setFont("Helvetica", 10)
    for item in order.items.all():
        p.drawString(40, y, item.product_name[:45])
        p.drawString(320, y, str(item.quantity))
        p.drawString(370, y, f"Rs.{item.price}")
        p.drawString(450, y, f"Rs.{item.subtotal}")
        y -= 16
        if y < 80:
            p.showPage()
            y = height - 60

    y -= 10
    p.line(40, y, width - 40, y)
    y -= 20
    p.setFont("Helvetica-Bold", 12)
    p.drawString(370, y, "Total:")
    p.drawString(450, y, f"Rs.{order.total}")

    p.showPage()
    p.save()
    buffer.seek(0)
    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="invoice-{order.id}.pdf"'
    return response


@login_required
def notifications_list(request):
    notifications = request.user.notifications.all()[:30]
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return render(request, "daraz/notifications.html", {"notifications": notifications})


def toggle_compare(request, pk):
    compare = request.session.get("compare", [])
    if pk in compare:
        compare.remove(pk)
    else:
        if len(compare) >= 4:
            compare.pop(0)
        compare.append(pk)
    request.session["compare"] = compare
    request.session.modified = True
    return redirect(request.META.get("HTTP_REFERER", "home"))


def compare_page(request):
    compare_ids = request.session.get("compare", [])
    products = Product.objects.filter(id__in=compare_ids)
    return render(request, "daraz/compare.html", {"products": products})


def cart_bulk_remove(request):
    if request.method == "POST":
        cart = request.session.get("cart", {})
        selected = request.POST.getlist("selected")
        for pid in selected:
            cart.pop(pid, None)
        request.session["cart"] = cart
        request.session.modified = True
        messages.success(request, "Selected items removed from cart.")
    return redirect("cart")


def search_suggest(request):
    q = request.GET.get("q", "").strip()
    if not q or len(q) < 2:
        return JsonResponse({"results": []})
    names = list(Product.objects.filter(name__icontains=q).values_list("name", flat=True)[:6])
    return JsonResponse({"results": names})


@login_required
def become_seller(request):
    existing = SellerAccount.objects.filter(user=request.user).first()
    if existing:
        return redirect("seller_dashboard")

    if request.method == "POST":
        account_type = request.POST.get("account_type", "individual")
        seller = SellerAccount.objects.create(
            user=request.user,
            account_type=account_type,
            business_name=request.POST.get("business_name", ""),
            phone=request.POST.get("phone", ""),
        )
        messages.success(request, "Your seller application has been submitted. We'll review it shortly.")
        return redirect("seller_dashboard")

    return render(request, "daraz/become_seller.html")


@login_required
def seller_dashboard(request):
    seller = get_object_or_404(SellerAccount, user=request.user)
    products = Product.objects.filter(seller_account=seller)

    order_items = OrderItem.objects.filter(product__seller_account=seller).select_related("order", "product")
    total_sales = sum(i.subtotal for i in order_items)
    commission_owed = round(total_sales * seller.commission_rate / 100, 2)
    net_earnings = total_sales - commission_owed

    return render(request, "daraz/seller_dashboard.html", {
        "seller": seller,
        "products": products,
        "order_items": order_items.order_by("-order__created_at")[:20],
        "total_sales": total_sales,
        "commission_owed": commission_owed,
        "net_earnings": net_earnings,
        "product_count": products.count(),
    })


@login_required
def seller_add_product(request):
    seller = get_object_or_404(SellerAccount, user=request.user, status="approved")
    if request.method == "POST":
        Product.objects.create(
            name=request.POST.get("name"),
            image_url=request.POST.get("image_url"),
            price=request.POST.get("price"),
            old_price=request.POST.get("old_price") or None,
            discount_percent=request.POST.get("discount_percent") or 0,
            category=request.POST.get("category"),
            stock=request.POST.get("stock") or 0,
            description=request.POST.get("description", ""),
            seller_account=seller,
            seller_name=seller.display_name,
        )
        messages.success(request, "Product added to your store.")
        return redirect("seller_dashboard")
    return render(request, "daraz/seller_add_product.html", {
        "categories": Product.CATEGORY_CHOICES,
    })


@login_required
def seller_edit_product(request, pk):
    seller = get_object_or_404(SellerAccount, user=request.user, status="approved")
    product = get_object_or_404(Product, pk=pk, seller_account=seller)
    if request.method == "POST":
        product.name = request.POST.get("name")
        product.image_url = request.POST.get("image_url")
        product.price = request.POST.get("price")
        product.old_price = request.POST.get("old_price") or None
        product.discount_percent = request.POST.get("discount_percent") or 0
        product.category = request.POST.get("category")
        product.stock = request.POST.get("stock") or 0
        product.description = request.POST.get("description", "")
        product.save()
        messages.success(request, "Product updated.")
        return redirect("seller_dashboard")
    return render(request, "daraz/seller_add_product.html", {
        "categories": Product.CATEGORY_CHOICES,
        "product": product,
    })


@login_required
def seller_delete_product(request, pk):
    seller = get_object_or_404(SellerAccount, user=request.user, status="approved")
    Product.objects.filter(pk=pk, seller_account=seller).delete()
    messages.success(request, "Product removed from your store.")
    return redirect("seller_dashboard")


def product_quick_view(request, pk):
    product = get_object_or_404(Product, pk=pk)
    return render(request, "daraz/partials/quick_view.html", {"product": product})
