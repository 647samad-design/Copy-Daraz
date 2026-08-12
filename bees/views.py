from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponse
from django.core.paginator import Paginator
from django.db.models import Q, Sum, Avg, Count, F


def with_ratings(queryset):
    """Annotate a Product queryset with avg_rating/review_count in one query,
    instead of each product template tag hitting the DB separately (N+1)."""
    return queryset.annotate(
        avg_rating=Avg("reviews__rating"),
        review_count=Count("reviews", distinct=True),
    )
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
    Notification, SearchLog, SellerAccount, SellerReview, ReturnRequest, SiteSettings, AuditLog,
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
    flash_sale_products = with_ratings(Product.objects.filter(is_flash_sale=True, approval_status="approved"))[:8]
    just_for_you_products = with_ratings(Product.objects.filter(approval_status="approved")).order_by("-created_at")[:16]
    categories = [
        {
            "slug": slug,
            "label": label,
            "image": f"https://picsum.photos/id/{CATEGORY_IMAGE_IDS.get(slug, 10)}/200/150",
        }
        for slug, label in Product.CATEGORY_CHOICES
    ]
    return render(request, "bees/index.html", {
        "flash_sale_products": flash_sale_products,
        "just_for_you_products": just_for_you_products,
        "categories": categories,
    })


def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)

    if product.approval_status != "approved":
        is_owner_seller = (
            product.seller_account and request.user.is_authenticated
            and product.seller_account.user_id == request.user.id
        )
        if not (is_owner_seller or request.user.is_staff):
            from django.http import Http404
            raise Http404("This product is not available.")

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
    related_products = with_ratings(Product.objects.filter(category=product.category, approval_status="approved").exclude(pk=product.pk))[:6]
    questions = product.questions.all()

    recent_ids = request.session.get("recently_viewed", [])
    recent_ids = [i for i in recent_ids if i != product.id]
    recent_ids.insert(0, product.id)
    request.session["recently_viewed"] = recent_ids[:10]
    request.session.modified = True
    recently_viewed = with_ratings(Product.objects.filter(id__in=recent_ids[1:7]))

    return render(request, "bees/product_detail.html", {
        "product": product,
        "reviews": reviews,
        "in_wishlist": in_wishlist,
        "gallery": gallery,
        "related_products": related_products,
        "questions": questions,
        "recently_viewed": recently_viewed,
    })


def category_products(request, category):
    products = with_ratings(Product.objects.filter(category=category, approval_status="approved"))
    sort = request.GET.get("sort", "")
    products = _apply_sort(products, sort)
    paginator = Paginator(products, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    label = dict(Product.CATEGORY_CHOICES).get(category, category)
    return render(request, "bees/category.html", {
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
    results = with_ratings(Product.objects.filter(name__icontains=query, approval_status="approved")) if query else Product.objects.none()
    sort = request.GET.get("sort", "")
    results = _apply_sort(results, sort)
    paginator = Paginator(results, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "bees/search.html", {
        "products": page_obj,
        "page_obj": page_obj,
        "query": query,
        "current_sort": sort,
    })


def all_products(request):
    products = with_ratings(Product.objects.filter(approval_status="approved"))
    sort = request.GET.get("sort", "")
    products = _apply_sort(products, sort)
    paginator = Paginator(products, 16)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "bees/all_products.html", {
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
        user_type = request.POST.get("user_type", "buyer")

        if not username or not password:
            messages.error(request, "Username and password are required.")
        elif password != confirm:
            messages.error(request, "Passwords do not match.")
        elif User.objects.filter(username=username).exists():
            messages.error(request, "That username is already taken.")
        elif user_type in ("individual", "organization") and not request.POST.get("phone", "").strip():
            messages.error(request, "Phone number is required for seller accounts.")
        else:
            user = User.objects.create_user(username=username, email=email, password=password)
            code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
            ref = request.GET.get("ref") or request.POST.get("ref", "")
            profile = Profile.objects.create(user=user, referral_code=code, referred_by=ref)
            if ref:
                referrer_profile = Profile.objects.filter(referral_code=ref).first()
                if referrer_profile:
                    referrer_coupon_code = "REF-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
                    new_user_coupon_code = "WELCOME-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
                    Coupon.objects.create(code=referrer_coupon_code, percent_off=10)
                    Coupon.objects.create(code=new_user_coupon_code, percent_off=10)
                    Notification.objects.create(
                        user=referrer_profile.user,
                        message=f"{username} joined using your referral link! Here's a 10% off code for you: {referrer_coupon_code}",
                        link="/profile/",
                    )
                    Notification.objects.create(
                        user=user,
                        message=f"Welcome! Here's a 10% off code for your first order: {new_user_coupon_code}",
                        link="/cart/",
                    )

            if user_type in ("individual", "organization"):
                SellerAccount.objects.create(
                    user=user,
                    account_type=user_type,
                    full_name=request.POST.get("full_name", ""),
                    business_name=request.POST.get("business_name", ""),
                    organization_name=request.POST.get("organization_name", ""),
                    phone=request.POST.get("phone", ""),
                    cnic=request.POST.get("cnic", ""),
                    business_address=request.POST.get("business_address", ""),
                    city=request.POST.get("city", ""),
                    country=request.POST.get("country", "Pakistan"),
                    store_description=request.POST.get("store_description", ""),
                    product_categories=request.POST.get("product_categories", ""),
                    brand_info=request.POST.get("brand_info", ""),
                    tax_info=request.POST.get("tax_info", ""),
                    bank_details=request.POST.get("bank_details", ""),
                    business_certificate=request.FILES.get("business_certificate"),
                    id_document=request.FILES.get("id_document"),
                    store_logo=request.FILES.get("store_logo"),
                    store_banner=request.FILES.get("store_banner"),
                )
                AuditLog.objects.create(user=user, action=f"Submitted {user_type} seller application")

            auth_login(request, user)
            if email:
                _send_verification_email(request, user)
            AuditLog.objects.create(user=user, action="Account created")

            if user_type in ("individual", "organization"):
                messages.success(request, "Account created! Your seller application is pending review.")
                return redirect("seller_dashboard")

            messages.success(request, "Account created. Welcome to 19Bees.")
            return redirect("home")

    return render(request, "bees/signup.html", {
        "google_client_id": settings.GOOGLE_CLIENT_ID,
        "ref_code": request.GET.get("ref", ""),
        "categories": Product.CATEGORY_CHOICES,
    })


def _send_verification_email(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    scheme = "https" if request.is_secure() else "http"
    link = f"{scheme}://{request.get_host()}/verify-email/{uid}/{token}/"
    try:
        html_body = render_to_string("bees/emails/verify_email.html", {"user": user, "link": link})
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
    next_url = request.POST.get("next") or request.GET.get("next") or "home"
    if request.user.is_authenticated:
        return redirect(next_url)

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            auth_login(request, user)
            return redirect(next_url)
        messages.error(request, "Incorrect username or password.")

    return render(request, "bees/login.html", {
        "google_client_id": settings.GOOGLE_CLIENT_ID,
        "next": next_url,
    })


def logout_view(request):
    auth_logout(request)
    return redirect("home")


@csrf_exempt
def google_auth(request):
    """
    Receives the Google Identity Services credential from the frontend.
    The <div id="g_id_onload" data-login_uri="..."> flow makes Google's
    library submit a real browser POST here and then expects an HTTP
    redirect back — NOT a JSON body — otherwise the user sees raw JSON
    text on screen instead of being logged in. Requires GOOGLE_CLIENT_ID
    to be configured.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    next_url = request.POST.get("next") or request.GET.get("next") or "home"

    if not settings.GOOGLE_CLIENT_ID:
        messages.error(request, "Google sign-in is not configured on this server.")
        return redirect("login")

    token = request.POST.get("credential")
    if not token:
        messages.error(request, "Google sign-in failed: missing credential.")
        return redirect("login")

    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as google_requests

        idinfo = id_token.verify_oauth2_token(
            token, google_requests.Request(), settings.GOOGLE_CLIENT_ID
        )
    except Exception:
        messages.error(request, "Google sign-in failed: invalid token.")
        return redirect("login")

    email = idinfo.get("email")
    name = idinfo.get("name", email.split("@")[0] if email else "google_user")

    if not email:
        messages.error(request, "Your Google account has no email on file.")
        return redirect("login")

    user, created = User.objects.get_or_create(
        username=email,
        defaults={"email": email, "first_name": name},
    )
    auth_login(request, user)
    return redirect(next_url)


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


def _is_ajax(request):
    return (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
    )


def add_to_cart(request, pk):
    product = get_object_or_404(Product, pk=pk)

    if product.stock <= 0:
        msg = f"{product.name} is out of stock."
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect(request.POST.get("next") or request.GET.get("next") or "cart")

    cart = request.session.get("cart", {})
    key = str(pk)
    qty = int(request.POST.get("quantity", 1)) if request.method == "POST" else 1
    new_qty = cart.get(key, 0) + qty
    warning = None
    if new_qty > product.stock:
        new_qty = product.stock
        warning = f"Only {product.stock} of {product.name} left in stock."
    cart[key] = new_qty
    request.session["cart"] = cart
    request.session.modified = True

    cart_total_count = sum(cart.values())

    if _is_ajax(request):
        return JsonResponse({
            "ok": True,
            "message": f"{product.name} added to cart.",
            "warning": warning,
            "cart_count": cart_total_count,
            "product_id": product.id,
            "product_qty": new_qty,
        })

    if warning:
        messages.warning(request, warning)
    messages.success(request, f"{product.name} added to cart.")
    next_url = request.POST.get("next") or request.GET.get("next") or "cart"
    return redirect(next_url)


def update_cart_item(request, pk):
    cart = request.session.get("cart", {})
    key = str(pk)
    action = request.POST.get("action")
    warning = None
    if key in cart:
        if action == "increase":
            product = Product.objects.filter(pk=pk).first()
            if product and cart[key] >= product.stock:
                warning = f"Only {product.stock} of {product.name} available."
            else:
                cart[key] += 1
        elif action == "decrease":
            cart[key] -= 1
            if cart[key] <= 0:
                del cart[key]
        elif action == "remove":
            del cart[key]
    request.session["cart"] = cart
    request.session.modified = True

    if _is_ajax(request):
        items, total, count = _get_cart_items(request)
        row = next((i for i in items if str(i["product"].id) == key), None)
        return JsonResponse({
            "ok": True,
            "warning": warning,
            "cart_count": count,
            "cart_total": str(total),
            "removed": row is None,
            "product_id": pk,
            "product_qty": row["qty"] if row else 0,
            "product_subtotal": str(row["subtotal"]) if row else "0",
        })
    return redirect("cart")


def cart_view(request):
    items, total, count = _get_cart_items(request)
    return render(request, "bees/cart.html", {
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

        order = Order.objects.create(
            user=request.user,
            guest_email="",
            full_name=request.POST.get("full_name", request.user.username),
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
                html_body = render_to_string("bees/emails/order_confirmation.html", {
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

    return render(request, "bees/checkout.html", {
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
    return render(request, "bees/order_success.html", {"order": order})


@login_required
def request_return(request, item_id):
    item = get_object_or_404(OrderItem, pk=item_id, order__user=request.user)
    if item.order.status != "delivered":
        messages.error(request, "Returns can only be requested for delivered orders.")
        return redirect("my_orders")
    if item.return_requests.exclude(status="rejected").exists():
        messages.error(request, "A return request already exists for this item.")
        return redirect("my_orders")

    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        refund_method = request.POST.get("refund_method", "original_payment")
        if not reason:
            messages.error(request, "Please describe the reason for your return.")
        else:
            ReturnRequest.objects.create(
                order_item=item, user=request.user, reason=reason, refund_method=refund_method,
            )
            messages.success(request, "Return request submitted. We'll review it shortly.")
            return redirect("my_orders")

    return render(request, "bees/request_return.html", {"item": item})


@login_required
def buy_again(request, order_id):
    order = get_object_or_404(Order, pk=order_id, user=request.user)
    cart = request.session.get("cart", {})
    added, skipped = 0, 0
    for item in order.items.select_related("product"):
        if not item.product or item.product.stock <= 0:
            skipped += 1
            continue
        key = str(item.product.id)
        cart[key] = min(cart.get(key, 0) + item.quantity, item.product.stock)
        added += 1
    request.session["cart"] = cart
    request.session.modified = True
    if added:
        messages.success(request, f"{added} item(s) added back to your cart.")
    if skipped:
        messages.warning(request, f"{skipped} item(s) are no longer available and were skipped.")
    return redirect("cart")


@login_required
def my_orders(request):
    orders = Order.objects.filter(user=request.user).prefetch_related(
        "items", "items__product", "items__return_requests"
    ).order_by("-created_at")
    return render(request, "bees/my_orders.html", {"orders": orders})


@login_required
def cancel_order(request, pk):
    order = get_object_or_404(Order, pk=pk, user=request.user)
    if request.method == "POST" and order.is_cancellable:
        order.status = "cancelled"
        order.save(update_fields=["status"])
        for item in order.items.select_related("product"):
            if item.product:
                item.product.stock += item.quantity
                item.product.save(update_fields=["stock"])
        messages.success(request, f"Order #{order.id} has been cancelled.")
    else:
        messages.error(request, "This order can no longer be cancelled.")
    return redirect("my_orders")


def set_language(request, lang_code):
    from .translations import TRANSLATIONS
    if lang_code in TRANSLATIONS:
        request.session["site_lang"] = lang_code
    next_url = request.META.get("HTTP_REFERER", "/")
    return redirect(next_url)


def help_support(request):
    faqs = [
        ("How do I place an order?", "Add products to your cart, go to Cart, click 'Proceed to Checkout', fill in your delivery details and confirm."),
        ("What payment methods are available?", "Cash on delivery, credit/debit card, and 19Bees wallet."),
        ("How can I track my order?", "Go to 'My orders' from the top menu after logging in to see all your past orders and their status."),
        ("Can I return a product?", "Yes — go to your order in 'My orders' and request a return within 7-14 days of delivery. Our team will review and follow up."),
        ("How do I change the site language?", "Use the 'Change language' option in the top bar to switch between English, Urdu, and Roman Urdu."),
        ("I forgot my password, what do I do?", "Click 'Forgot password?' on the login page and follow the link sent to your email to set a new password."),
    ]
    return render(request, "bees/help.html", {"faqs": faqs})


def sell_on_bees(request):
    return render(request, "bees/static_page.html", {
        "page_title": "Sell on 19Bees",
        "sections": [
            ("Reach more buyers", "Register your shop and list your products in front of shoppers browsing every category on 19Bees — from electronics to fashion to groceries."),
            ("Two account types", "Individual sellers pay a 10% platform commission. Organizations / registered businesses pay 20%, with rates that improve as your sales grow."),
            ("Simple onboarding", "Apply below, get approved, and start listing products from your own seller dashboard — track sales, commission, and earnings in one place."),
        ],
        "cta_url": "/become-seller/",
        "cta_label": "Apply to become a seller",
    })


def about_us(request):
    return render(request, "bees/static_page.html", {
        "page_title": "About 19Bees",
        "sections": [
            ("Who we are", "19Bees is an online marketplace connecting buyers with individual sellers and businesses across every category — skincare, electronics, fashion, groceries, and more."),
            ("Our mission", "We're building a trusted, easy-to-use platform where anyone can shop with confidence and where sellers of any size can grow their business."),
        ],
    })


def terms_page(request):
    return render(request, "bees/static_page.html", {
        "page_title": "Terms and Conditions",
        "sections": [
            ("Using 19Bees", "By creating an account or placing an order on 19Bees, you agree to these terms. Please use the platform responsibly and in accordance with applicable laws."),
            ("Accounts", "You're responsible for keeping your account credentials secure. Seller accounts are subject to review and approval before going live."),
            ("Orders and payments", "Orders are confirmed once payment or cash-on-delivery details are submitted. Pricing and availability may change without notice."),
        ],
    })


def privacy_page(request):
    return render(request, "bees/static_page.html", {
        "page_title": "Privacy Policy",
        "sections": [
            ("What we collect", "We collect the information you provide when creating an account, placing an order, or applying to sell — such as your name, email, address, and payment details."),
            ("How we use it", "Your information is used to process orders, provide support, and improve your experience on 19Bees. We do not sell your personal data to third parties."),
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
    return render(request, "bees/wishlist.html", {"items": items})


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
    return render(request, "bees/profile.html", {
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
    products = with_ratings(Product.objects.filter(seller_name=seller_name, approval_status="approved"))
    return render(request, "bees/store.html", {
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
    return render(request, "bees/notifications.html", {"notifications": notifications})


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
    products = with_ratings(Product.objects.filter(id__in=compare_ids))
    return render(request, "bees/compare.html", {"products": products})


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
    names = list(Product.objects.filter(name__icontains=q, approval_status="approved").values_list("name", flat=True)[:6])
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

    return render(request, "bees/become_seller.html")


@login_required
def update_fulfillment_status(request, item_id):
    item = get_object_or_404(OrderItem, pk=item_id, product__seller_account__user=request.user)
    new_status = request.POST.get("fulfillment_status")
    if request.method == "POST" and new_status in dict(OrderItem.FULFILLMENT_CHOICES):
        item.fulfillment_status = new_status
        item.save(update_fields=["fulfillment_status"])
        messages.success(request, f"Marked '{item.product_name}' as {item.get_fulfillment_status_display()}.")
    return redirect("seller_dashboard")


@login_required
def seller_dashboard(request):
    seller = get_object_or_404(SellerAccount, user=request.user)
    products = Product.objects.filter(seller_account=seller)

    order_items_qs = OrderItem.objects.filter(product__seller_account=seller).select_related(
        "order", "product"
    ).order_by("-order__created_at")
    order_items = list(order_items_qs)
    total_sales = sum(i.subtotal for i in order_items)
    commission_owed = round(total_sales * seller.commission_rate / 100, 2)
    net_earnings = total_sales - commission_owed

    from datetime import timedelta
    from django.utils import timezone
    today = timezone.localdate()
    daily_sales = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_total = sum(
            it.subtotal for it in order_items if it.order.created_at.date() == day
        )
        daily_sales.append({"label": day.strftime("%a"), "amount": float(day_total)})
    max_daily = max([d["amount"] for d in daily_sales] or [1]) or 1
    for d in daily_sales:
        d["pct"] = round((d["amount"] / max_daily) * 100, 1) if max_daily else 0

    top_products = (
        products.annotate(units_sold=Sum("orderitem__quantity"))
        .filter(units_sold__gt=0)
        .order_by("-units_sold")[:5]
    )
    low_stock_products = products.filter(stock__gt=0, stock__lte=5)
    out_of_stock_products = products.filter(stock__lte=0)

    return render(request, "bees/seller_dashboard.html", {
        "seller": seller,
        "products": products,
        "order_items": order_items[:20],
        "total_sales": total_sales,
        "commission_owed": commission_owed,
        "net_earnings": net_earnings,
        "product_count": products.count(),
        "daily_sales": daily_sales,
        "top_products": top_products,
        "low_stock_products": low_stock_products,
        "out_of_stock_products": out_of_stock_products,
    })


@login_required
def seller_add_product(request):
    seller = get_object_or_404(SellerAccount, user=request.user, status="approved")
    if request.method == "POST":
        image_url = request.POST.get("image_url", "").strip()
        uploaded = request.FILES.get("image_file")
        if uploaded:
            from django.core.files.storage import default_storage
            path = default_storage.save(f"products/{uploaded.name}", uploaded)
            image_url = default_storage.url(path)

        Product.objects.create(
            name=request.POST.get("name"),
            image_url=image_url,
            price=request.POST.get("price"),
            old_price=request.POST.get("old_price") or None,
            discount_percent=request.POST.get("discount_percent") or 0,
            category=request.POST.get("category"),
            stock=request.POST.get("stock") or 0,
            description=request.POST.get("description", ""),
            seller_account=seller,
            seller_name=seller.display_name,
            approval_status="pending",
        )
        messages.success(request, "Product submitted for review. It will go live once approved by an admin.")
        return redirect("seller_dashboard")
    return render(request, "bees/seller_add_product.html", {
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
    return render(request, "bees/seller_add_product.html", {
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
    return render(request, "bees/partials/quick_view.html", {"product": product})


def _is_owner(user):
    return user.is_authenticated and user.is_staff


@login_required
def owner_dashboard(request):
    if not _is_owner(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Staff access only.")

    total_customers = User.objects.filter(seller_account__isnull=True).count()
    total_sellers = SellerAccount.objects.filter(account_type="individual").count()
    total_organizations = SellerAccount.objects.filter(account_type="organization").count()
    total_products = Product.objects.count()
    total_orders = Order.objects.count()

    from datetime import timedelta
    from django.utils import timezone
    from django.db.models.functions import TruncDate

    today = timezone.localdate()
    start_date = today - timedelta(days=6)
    non_cancelled = Order.objects.exclude(status="cancelled")

    # Two lightweight aggregate queries (instead of looping every order's
    # .total property, which used to run a query per order): one sums
    # item subtotals grouped by day, the other sums discounts grouped by
    # day. Combined in Python below - avoids double-counting discount_amount
    # that a single joined query would cause.
    subtotal_by_day = {
        row["day"]: row["subtotal"] or 0
        for row in non_cancelled.filter(created_at__date__gte=start_date)
        .annotate(day=TruncDate("created_at")).values("day")
        .annotate(subtotal=Sum(F("items__price") * F("items__quantity")))
    }
    discount_by_day = {
        row["day"]: row["discount"] or 0
        for row in non_cancelled.filter(created_at__date__gte=start_date)
        .annotate(day=TruncDate("created_at")).values("day")
        .annotate(discount=Sum("discount_amount"))
    }
    total_agg = non_cancelled.aggregate(
        subtotal=Sum(F("items__price") * F("items__quantity")),
    )
    total_discount = non_cancelled.aggregate(discount=Sum("discount_amount"))["discount"] or 0
    total_revenue = max((total_agg["subtotal"] or 0) - total_discount, 0)

    daily_revenue = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_total = max(float(subtotal_by_day.get(day, 0)) - float(discount_by_day.get(day, 0)), 0)
        daily_revenue.append({"label": day.strftime("%a"), "date": day.strftime("%d %b"), "amount": day_total})
    max_daily = max([d["amount"] for d in daily_revenue] or [1]) or 1
    for d in daily_revenue:
        d["pct"] = round((d["amount"] / max_daily) * 100, 1) if max_daily else 0
    pending_sellers = SellerAccount.objects.filter(status="pending")
    active_sellers = SellerAccount.objects.filter(status="approved").count()
    inactive_sellers = SellerAccount.objects.filter(status__in=["rejected", "suspended"]).count()
    recent_orders = Order.objects.prefetch_related("items").order_by("-created_at")[:10]
    approved_sellers = SellerAccount.objects.filter(status="approved")
    top_sellers = sorted(approved_sellers, key=lambda s: s.lifetime_sales, reverse=True)[:5]

    low_stock_products = Product.objects.filter(stock__gt=0, stock__lte=5).order_by("stock")[:10]
    out_of_stock_products = Product.objects.filter(stock__lte=0).order_by("-id")[:10]
    pending_products = Product.objects.filter(approval_status="pending").order_by("-id")[:10]
    pending_products_count = Product.objects.filter(approval_status="pending").count()
    low_stock_count = Product.objects.filter(stock__gt=0, stock__lte=5).count()
    out_of_stock_count = Product.objects.filter(stock__lte=0).count()

    seller_earnings = []
    total_commission_earned = 0
    total_still_owed = 0
    for s in top_sellers:
        units_sold = OrderItem.objects.filter(product__seller_account=s).aggregate(
            total=Sum("quantity")
        )["total"] or 0
        seller_earnings.append({
            "seller": s, "sales": float(s.lifetime_sales), "rate": s.effective_commission_rate,
            "commission": round(float(s.lifetime_sales) * s.effective_commission_rate / 100, 2),
            "net": s.net_earnings, "owed": s.amount_owed, "units_sold": units_sold,
        })
    for s in approved_sellers:
        sales = float(s.lifetime_sales)
        commission = sales * s.effective_commission_rate / 100
        total_commission_earned += commission
        total_still_owed += s.amount_owed

    return render(request, "bees/owner_dashboard.html", {
        "total_customers": total_customers,
        "total_sellers": total_sellers,
        "total_organizations": total_organizations,
        "total_products": total_products,
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "pending_sellers": pending_sellers,
        "active_sellers": active_sellers,
        "inactive_sellers": inactive_sellers,
        "recent_orders": recent_orders,
        "top_sellers": top_sellers,
        "seller_earnings": seller_earnings,
        "total_commission_earned": round(total_commission_earned, 2),
        "total_still_owed": round(total_still_owed, 2),
        "low_stock_products": low_stock_products,
        "out_of_stock_products": out_of_stock_products,
        "pending_products": pending_products,
        "pending_products_count": pending_products_count,
        "low_stock_count": low_stock_count,
        "out_of_stock_count": out_of_stock_count,
        "daily_revenue": daily_revenue,
    })
