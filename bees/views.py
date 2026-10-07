import logging
import random
import secrets
import string
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q, Sum, Avg, Count, F
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import order_emails, payments
from .models import (
    Product, Review, Order, OrderItem, Wishlist, Coupon,
    ProductImage, Profile, Address, Question, NewsletterSubscriber,
    Notification, SearchLog, SellerAccount, SellerReview, ReturnRequest, SiteSettings, AuditLog,
    OrganizationMember, ChatThread, ChatMessage,
)
from .ratelimit import ratelimit
from .security import (
    safe_next_url, redirect_back, validate_image_upload, validate_document_upload, random_upload_name,
)
from .templatetags.bees_extras import money

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def with_ratings(queryset):
    """Annotate a Product queryset with avg_rating/review_count in one query,
    instead of each product template tag hitting the DB separately (N+1)."""
    return queryset.annotate(
        avg_rating=Avg("reviews__rating"),
        review_count=Count("reviews", distinct=True),
    )


def get_seller_account_for_user(user):
    """Returns the SellerAccount this user can act on behalf of - either
    because they own it, or because an organization added them as a team
    member. Used so seller dashboard/product/order views work the same way
    for both the account owner and any team member."""
    account = SellerAccount.objects.filter(user=user).first()
    if account:
        return account, "owner"
    membership = user.organization_memberships.select_related("organization").first()
    if membership:
        return membership.organization, membership.role
    return None, None


def is_speculative(request):
    """True for browser prefetch/prerender requests (sent when a visitor
    hovers a link). Those shouldn't count as views or searches."""
    return request.headers.get("Sec-Purpose", "").startswith("prefetch") or request.headers.get("Purpose") == "prefetch"


def _brand():
    return SiteSettings.load()


def _store_name():
    return _brand().site_name


def _parse_decimal(value, field, minimum=Decimal("0"), required=True):
    if value in (None, ""):
        if required:
            raise ValidationError(f"{field} is required.")
        return None
    try:
        number = Decimal(str(value).strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{field} must be a number.")
    if number < minimum:
        raise ValidationError(f"{field} must be at least {minimum}.")
    return number


def _parse_int(value, field, minimum=0, maximum=None, default=0):
    if value in (None, ""):
        return default
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValidationError(f"{field} must be a whole number.")
    if number < minimum or (maximum is not None and number > maximum):
        raise ValidationError(f"{field} must be between {minimum} and {maximum}." if maximum is not None else f"{field} must be at least {minimum}.")
    return number


def _apply_sort(qs, sort):
    if sort == "price_asc":
        return qs.order_by("price")
    if sort == "price_desc":
        return qs.order_by("-price")
    if sort == "newest":
        return qs.order_by("-created_at")
    if sort == "rating":
        return qs.order_by(F("avg_rating").desc(nulls_last=True), "-id")
    return qs.order_by("-id")


def _apply_filters(qs, request):
    """Applies price range, minimum rating, seller and in-stock filters
    from GET params, shared across all_products/category_products/search."""
    min_price = request.GET.get("min_price")
    max_price = request.GET.get("max_price")
    min_rating = request.GET.get("min_rating")
    seller = request.GET.get("seller")
    in_stock = request.GET.get("in_stock")

    if min_price:
        try:
            qs = qs.filter(price__gte=Decimal(min_price))
        except (InvalidOperation, ValueError):
            pass
    if max_price:
        try:
            qs = qs.filter(price__lte=Decimal(max_price))
        except (InvalidOperation, ValueError):
            pass
    if seller:
        qs = qs.filter(seller_name=seller)
    if in_stock:
        qs = qs.filter(stock__gt=0)
    if min_rating:
        try:
            qs = qs.filter(avg_rating__gte=float(min_rating))
        except ValueError:
            pass
    return qs


def _filter_context(request, base_qs):
    """Sellers list for the filter sidebar, and the current filter values
    so the form and pagination links can stay populated across requests."""
    return {
        "sellers": base_qs.order_by("seller_name").values_list("seller_name", flat=True).distinct(),
        "f_min_price": request.GET.get("min_price", ""),
        "f_max_price": request.GET.get("max_price", ""),
        "f_min_rating": request.GET.get("min_rating", ""),
        "f_seller": request.GET.get("seller", ""),
        "f_in_stock": request.GET.get("in_stock", ""),
    }




def _can_view_unapproved(user, product):
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    seller, _ = get_seller_account_for_user(user)
    return bool(seller and product.seller_account_id == seller.id)


def _send_html_email(subject, template, context, recipient):
    if not recipient:
        return
    try:
        context = {"brand": _brand(), **context}
        html_body = render_to_string(template, context)
        email = EmailMultiAlternatives(subject, strip_tags(html_body), None, [recipient])
        email.attach_alternative(html_body, "text/html")
        email.send(fail_silently=True)
    except Exception:
        logger.exception("Failed to send email '%s' to %s", subject, recipient)


def _send_order_confirmation(request, order):
    invoice_url = request.build_absolute_uri(reverse("invoice_pdf", args=[order.id]))
    if order.status == "pending":
        subject = f"We've received your {_store_name()} order #{order.id}"
    else:
        subject = f"Your {_store_name()} order #{order.id} is confirmed"
    _send_html_email(
        subject,
        "bees/emails/order_confirmation.html",
        {"order": order, "invoice_url": invoice_url},
        order.contact_email,
    )


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

def home(request):
    query = request.GET.get("q", "").strip()
    if query:
        return redirect(f"{reverse('search_products')}?{urlencode({'q': query})}")
    flash_sale_products = with_ratings(Product.objects.live().filter(is_flash_sale=True))[:10]
    just_for_you_products = with_ratings(Product.objects.live()).order_by("-created_at")[:15]
    return render(request, "bees/index.html", {
        "flash_sale_products": flash_sale_products,
        "just_for_you_products": just_for_you_products,
        "categories": _category_tiles(),
    })


def _category_tiles():
    """One tile per category that has products, pictured with one of its
    own products (so the picture always matches). Cached for 5 minutes."""
    tiles = cache.get("home:category_tiles")
    if tiles is None:
        images = {}
        for category, image in (
            Product.objects.live().exclude(image_url="")
            .order_by("category", "id").values_list("category", "image_url")
        ):
            images.setdefault(category, image)
        tiles = [
            {"slug": slug, "label": label, "image": images[slug]}
            for slug, label in Product.CATEGORY_CHOICES if slug in images
        ]
        cache.set("home:category_tiles", tiles, 300)
    return tiles




def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)

    if not product.is_live and not _can_view_unapproved(request.user, product):
        raise Http404("This product is not available.")

    if request.method == "POST":
        return _submit_review(request, product)

    reviews = product.reviews.all()
    in_wishlist = False
    can_review = False
    review_block = ""
    if request.user.is_authenticated:
        in_wishlist = Wishlist.objects.filter(user=request.user, product=product).exists()
        review_block = _review_block_reason(request.user, product)
        can_review = not review_block

    gallery = [product.image_url] + list(product.extra_images.values_list("image_url", flat=True))
    related_products = with_ratings(Product.objects.live().filter(category=product.category).exclude(pk=product.pk))[:6]
    questions = product.questions.all()

    recent_ids = request.session.get("recently_viewed", [])
    if not is_speculative(request):
        recent_ids = [i for i in recent_ids if i != product.id]
        recent_ids.insert(0, product.id)
        request.session["recently_viewed"] = recent_ids[:10]
        request.session.modified = True
    else:
        recent_ids = [product.id] + [i for i in recent_ids if i != product.id]
    recently_viewed = with_ratings(Product.objects.live().filter(id__in=recent_ids[1:7]))

    return render(request, "bees/product_detail.html", {
        "product": product,
        "reviews": reviews,
        "in_wishlist": in_wishlist,
        "can_review": can_review,
        "review_block": review_block,
        "gallery": gallery,
        "related_products": related_products,
        "questions": questions,
        "recently_viewed": recently_viewed,
    })


def _review_block_reason(user, product):
    """Why ``user`` can't review ``product`` (empty string if they can).
    Only customers who received the product can review it, and sellers
    can't review their own listings."""
    seller, _ = get_seller_account_for_user(user)
    if seller and product.seller_account_id == seller.id:
        return "You can't review your own product."
    if not OrderItem.objects.filter(order__user=user, product=product, order__status="delivered").exists():
        return "Only customers who bought this product can review it. You'll be able to once your order is delivered."
    return ""


def _submit_review(request, product):
    """One review per signed-in customer per product; a second submission
    updates the first. Marked 'verified purchase' if they bought it."""
    if not request.user.is_authenticated:
        messages.error(request, "Please sign in to write a review.")
        return redirect(f"{reverse('login')}?{urlencode({'next': reverse('product_detail', args=[product.pk])})}")
    try:
        rating = _parse_int(request.POST.get("rating"), "Rating", minimum=1, maximum=5, default=5)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect("product_detail", pk=product.pk)
    comment = request.POST.get("comment", "").strip()[:2000]
    if not comment:
        messages.error(request, "Please write a few words about the product.")
        return redirect("product_detail", pk=product.pk)
    blocked = _review_block_reason(request.user, product)
    if blocked:
        messages.error(request, blocked)
        return redirect("product_detail", pk=product.pk)
    verified = True
    Review.objects.update_or_create(
        product=product, user=request.user,
        defaults={
            "username": request.user.get_full_name() or request.user.username,
            "rating": rating,
            "comment": comment,
            "is_verified_purchase": verified,
        },
    )
    messages.success(request, "Thanks - your review has been posted.")
    return redirect("product_detail", pk=product.pk)


def _listing(request, base_qs, per_page):
    products = _apply_filters(with_ratings(base_qs), request)
    sort = request.GET.get("sort", "")
    products = _apply_sort(products, sort)
    page_obj = Paginator(products, per_page).get_page(request.GET.get("page"))
    context = {"products": page_obj, "page_obj": page_obj, "current_sort": sort}
    context.update(_filter_context(request, base_qs))
    return context


def category_products(request, category):
    labels = dict(Product.CATEGORY_CHOICES)
    if category not in labels:
        raise Http404("Unknown category.")
    context = _listing(request, Product.objects.live().filter(category=category), 12)
    context.update({"category": category, "category_label": labels[category]})
    return render(request, "bees/category.html", context)


def search_products(request):
    query = request.GET.get("q", "").strip()[:150]
    if query and not is_speculative(request):
        log, created = SearchLog.objects.get_or_create(query__iexact=query, defaults={"query": query})
        if not created:
            SearchLog.objects.filter(pk=log.pk).update(count=F("count") + 1)
    base_qs = (
        Product.objects.live().filter(Q(name__icontains=query) | Q(description__icontains=query))
        if query else Product.objects.none()
    )
    context = _listing(request, base_qs, 12)
    context["query"] = query
    return render(request, "bees/search.html", context)


def all_products(request):
    context = _listing(request, Product.objects.live(), 16)
    return render(request, "bees/all_products.html", context)


def product_quick_view(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if not product.is_live and not _can_view_unapproved(request.user, product):
        raise Http404("This product is not available.")
    return render(request, "bees/partials/quick_view.html", {"product": product})


def search_suggest(request):
    q = request.GET.get("q", "").strip()
    if not q or len(q) < 2:
        return JsonResponse({"results": []})
    names = list(Product.objects.live().filter(name__icontains=q).values_list("name", flat=True)[:6])
    return JsonResponse({"results": names})


def store_page(request, seller_name):
    products = with_ratings(Product.objects.live().filter(seller_name=seller_name))
    seller_account = SellerAccount.objects.filter(
        Q(business_name=seller_name) | Q(organization_name=seller_name),
        status="approved",
    ).first()
    return render(request, "bees/store.html", {
        "seller_name": seller_name,
        "products": products,
        "seller_account": seller_account,
    })


@ratelimit("ask_question", rate_limit=10, window_seconds=600, redirect_to="home")
def ask_question(request, pk):
    product = get_object_or_404(Product.objects.live(), pk=pk)
    if request.method == "POST":
        if not request.user.is_authenticated:
            messages.error(request, "Please sign in to ask a question.")
            return redirect(f"{reverse('login')}?{urlencode({'next': reverse('product_detail', args=[pk])})}")
        text = request.POST.get("question", "").strip()[:1000]
        if text:
            Question.objects.create(product=product, user=request.user, username=request.user.username, question=text)
            if product.seller_account_id:
                Notification.objects.create(
                    user_id=product.seller_account.user_id, link="/seller/dashboard/#questions",
                    message=f"New customer question on '{product.name[:120]}'.",
                )
            messages.success(request, "Your question has been posted. We'll notify you when it's answered.")
    return redirect("product_detail", pk=pk)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

_username_validator = UnicodeUsernameValidator()


@ratelimit("signup", rate_limit=5, window_seconds=300, redirect_to="signup",
           message="Too many signup attempts from this connection. Please wait a few minutes and try again.")
def signup_view(request):
    if request.user.is_authenticated:
        return redirect("home")

    form = {}
    if request.method == "POST":
        form = request.POST
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        confirm = request.POST.get("confirm_password", "")
        user_type = request.POST.get("user_type", "buyer")
        if user_type not in ("buyer", "individual", "organization"):
            user_type = "buyer"

        error = None
        try:
            if not username or not password or not email:
                raise ValidationError("Username, email and password are required.")
            _username_validator(username)
            if "@" in username:
                raise ValidationError("Usernames can't contain '@'. Use letters, numbers and . _ - only.")
            validate_email(email)
            if password != confirm:
                raise ValidationError("Passwords do not match.")
            if User.objects.filter(username__iexact=username).exists():
                raise ValidationError("That username is already taken.")
            if User.objects.filter(email__iexact=email).exists():
                raise ValidationError("An account with that email already exists. Try signing in or resetting your password.")
            if user_type in ("individual", "organization") and not request.POST.get("phone", "").strip():
                raise ValidationError("Phone number is required for seller accounts.")
            validate_password(password, User(username=username, email=email))
            for field in ("business_certificate", "id_document"):
                validate_document_upload(request.FILES.get(field))
            for field in ("store_logo", "store_banner"):
                validate_image_upload(request.FILES.get(field))
        except ValidationError as exc:
            error = " ".join(exc.messages)

        if error:
            messages.error(request, error)
        else:
            with transaction.atomic():
                user = User.objects.create_user(username=username, email=email, password=password)
                code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
                ref = (request.GET.get("ref") or request.POST.get("ref", ""))[:12]
                Profile.objects.create(user=user, referral_code=code, referred_by=ref)
                if ref:
                    _reward_referral(ref, user)

                if user_type in ("individual", "organization"):
                    SellerAccount.objects.create(
                        user=user,
                        account_type=user_type,
                        full_name=request.POST.get("full_name", "")[:150],
                        business_name=request.POST.get("business_name", "")[:150],
                        organization_name=request.POST.get("organization_name", "")[:150],
                        phone=request.POST.get("phone", "")[:30],
                        cnic=request.POST.get("cnic", "")[:30],
                        business_address=request.POST.get("business_address", "")[:255],
                        city=request.POST.get("city", "")[:100],
                        country=request.POST.get("country", "")[:100],
                        store_description=request.POST.get("store_description", ""),
                        product_categories=request.POST.get("product_categories", "")[:255],
                        brand_info=request.POST.get("brand_info", ""),
                        tax_info=request.POST.get("tax_info", "")[:100],
                        bank_details=request.POST.get("bank_details", "")[:255],
                        business_certificate=request.FILES.get("business_certificate"),
                        id_document=request.FILES.get("id_document"),
                        store_logo=request.FILES.get("store_logo"),
                        store_banner=request.FILES.get("store_banner"),
                    )
                    AuditLog.objects.create(user=user, action=f"Submitted {user_type} seller application")
                AuditLog.objects.create(user=user, action="Account created")

            auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            _send_verification_email(request, user)

            if user_type in ("individual", "organization"):
                messages.success(request, "Account created! Your seller application is pending review.")
                return redirect("seller_dashboard")

            messages.success(request, f"Welcome to {_store_name()}! We've emailed you a code to verify your address.")
            return redirect(safe_next_url(request, request.POST.get("next") or request.GET.get("next"), reverse("home")))

    return render(request, "bees/signup.html", {
        "next": safe_next_url(request, request.POST.get("next") or request.GET.get("next"), ""),
        "ref_code": request.GET.get("ref", "") or request.POST.get("ref", ""),
        "categories": Product.CATEGORY_CHOICES,
        "form": form,
    })


def _reward_referral(ref, new_user):
    """New customer who signed up with a referral link gets a single-use
    welcome coupon. The referrer is rewarded later, when this customer's
    first order is delivered (see models._reward_referrer_for)."""
    from datetime import timedelta
    from django.utils import timezone
    referrer_profile = Profile.objects.filter(referral_code=ref).exclude(user=new_user).first()
    if not referrer_profile:
        return
    new_user_coupon_code = "WELCOME-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    Coupon.objects.create(code=new_user_coupon_code, percent_off=10, usage_limit=1, per_user_limit=1,
                          expiry_date=timezone.localdate() + timedelta(days=60))
    Notification.objects.create(
        user=new_user,
        message=f"Welcome! Here's 10% off your first order: {new_user_coupon_code}",
        link="/cart/",
    )


def _send_verification_email(request, user):
    if not user.email:
        return
    code = f"{secrets.randbelow(1_000_000):06d}"
    cache.set(f"email_verify_code:{user.id}", code, timeout=900)  # valid for 15 minutes
    cache.delete(f"email_verify_attempts:{user.id}")
    _send_html_email(
        f"Your {_store_name()} verification code",
        "bees/emails/verify_email.html",
        {"user": user, "code": code},
        user.email,
    )


MAX_VERIFY_ATTEMPTS = 5


@login_required
def verify_email_code(request):
    profile, _ = Profile.objects.get_or_create(user=request.user, defaults={"referral_code": secrets.token_hex(4).upper()})
    if profile.email_verified:
        messages.info(request, "Your email is already verified.")
        return redirect("profile")

    if request.method == "POST":
        attempts_key = f"email_verify_attempts:{request.user.id}"
        entered = request.POST.get("code", "").strip()
        stored = cache.get(f"email_verify_code:{request.user.id}")
        attempts = cache.get(attempts_key, 0)
        if not stored:
            messages.error(request, "That code has expired. Please request a new one.")
        elif attempts >= MAX_VERIFY_ATTEMPTS:
            cache.delete(f"email_verify_code:{request.user.id}")
            messages.error(request, "Too many incorrect attempts. Please request a new code.")
        elif secrets.compare_digest(entered, stored):
            profile.email_verified = True
            profile.save(update_fields=["email_verified"])
            cache.delete(f"email_verify_code:{request.user.id}")
            cache.delete(attempts_key)
            messages.success(request, "Your email has been verified.")
            return redirect("profile")
        else:
            cache.set(attempts_key, attempts + 1, timeout=900)
            messages.error(request, "That code isn't right. Please check your email and try again.")

    return render(request, "bees/verify_email_code.html")


@login_required
@ratelimit("resend_verification", rate_limit=3, window_seconds=300, redirect_to="profile",
           message="Please wait a few minutes before requesting another code.")
def resend_verification(request):
    if request.method != "POST":
        return redirect("verify_email")
    _send_verification_email(request, request.user)
    messages.success(request, "A new verification code has been sent to your email.")
    return redirect("verify_email")


@ratelimit("login", rate_limit=8, window_seconds=300, redirect_to="login",
           message="Too many sign-in attempts from this connection. Please wait a few minutes and try again.")
def login_view(request):
    next_url = safe_next_url(request, request.POST.get("next") or request.GET.get("next"), reverse("home"))
    if request.user.is_authenticated:
        return redirect(next_url)

    if request.method == "POST":
        identifier = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        username = identifier
        if "@" in identifier:
            matches = list(User.objects.filter(email__iexact=identifier).values_list("username", flat=True)[:2])
            if len(matches) == 1:
                username = matches[0]
        user = authenticate(request, username=username, password=password)
        if user is not None:
            auth_login(request, user)
            if not request.POST.get("remember"):
                request.session.set_expiry(0)  # signed out when the browser closes
            return redirect(next_url)
        messages.error(request, "Incorrect username/email or password.")

    return render(request, "bees/login.html", {"next": next_url})


def logout_view(request):
    if request.method == "POST":
        auth_logout(request)
        messages.info(request, "You've been signed out.")
    return redirect("home")


@login_required
@require_POST
def redeem_points(request):
    """Converts reward points into store credit in whole units."""
    with transaction.atomic():
        profile = Profile.objects.select_for_update().filter(user=request.user).first()
        units = profile.loyalty_points // Profile.POINTS_PER_UNIT if profile else 0
        if not units:
            messages.error(request, f"You need at least {Profile.POINTS_PER_UNIT} points to redeem.")
            return redirect("profile")
        profile.loyalty_points -= units * Profile.POINTS_PER_UNIT
        profile.store_credit += Decimal(units)
        profile.save(update_fields=["loyalty_points", "store_credit"])
    messages.success(request, f"{money(Decimal(units))} added to your store credit. It'll be applied at your next checkout.")
    return redirect("profile")


@login_required
def profile_view(request):
    profile, _ = Profile.objects.get_or_create(user=request.user, defaults={"referral_code": secrets.token_hex(4).upper()})
    if request.method == "POST":
        new_email = request.POST.get("email", "").strip().lower()
        try:
            if new_email:
                validate_email(new_email)
                if User.objects.filter(email__iexact=new_email).exclude(pk=request.user.pk).exists():
                    raise ValidationError("That email is already used by another account.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("profile")
        old_email = request.user.email or ""
        email_changed = new_email != old_email.lower()
        request.user.first_name = request.POST.get("first_name", "")[:150]
        request.user.email = new_email
        request.user.save(update_fields=["first_name", "email"])
        profile.phone = request.POST.get("phone", "")[:30]
        if email_changed:
            profile.email_verified = False
        profile.save()
        if email_changed and old_email:
            # Tell the old address, so a hijacked account doesn't go unnoticed.
            _send_html_email("Your email address was changed", "bees/emails/security_notice.html", {
                "user": request.user, "event": "The email address on your account was changed",
                "detail": f"New address: {new_email or '(removed)'}. If this wasn't you, reset your password straight away and contact us.",
                "reset_url": request.build_absolute_uri(reverse("password_reset")),
            }, old_email)
            AuditLog.objects.create(user=request.user, action="Changed email address")
        if email_changed and new_email:
            _send_verification_email(request, request.user)
            messages.success(request, "Profile updated. We've sent a code to verify your new email.")
        else:
            messages.success(request, "Profile updated.")
        return redirect("profile")
    addresses = Address.objects.filter(user=request.user)
    orders_count = Order.objects.filter(user=request.user).count()
    redeemable_points = profile.loyalty_points - profile.loyalty_points % Profile.POINTS_PER_UNIT
    return render(request, "bees/profile.html", {
        "profile": profile,
        "points_per_unit": Profile.POINTS_PER_UNIT,
        "redeemable_points": redeemable_points,
        "redeemable_credit": Decimal(redeemable_points // Profile.POINTS_PER_UNIT),
        "addresses": addresses,
        "orders_count": orders_count,
    })


ADDRESS_FIELDS = ("full_name", "phone", "address", "city", "state", "postal_code", "country")


@login_required
@require_POST
def add_address(request):
    data = {f: request.POST.get(f, "").strip() for f in ADDRESS_FIELDS}
    data["country"] = data["country"].upper()[:2]
    if not all(data[f] for f in ("full_name", "address", "city", "country")):
        messages.error(request, "Please fill in name, street address, city and country.")
        return redirect("profile")
    first = not Address.objects.filter(user=request.user).exists()
    Address.objects.create(user=request.user, label=request.POST.get("label", "Home")[:30] or "Home", is_default=first, **data)
    messages.success(request, "Address saved.")
    return redirect("profile")


@login_required
@require_POST
def delete_address(request, pk):
    Address.objects.filter(pk=pk, user=request.user).delete()
    messages.success(request, "Address removed.")
    return redirect("profile")


@login_required
def notifications_list(request):
    notifications = list(request.user.notifications.all()[:30])
    if not is_speculative(request):  # a hover-prefetch isn't the user reading them
        request.user.notifications.filter(is_read=False).update(is_read=True)
    return render(request, "bees/notifications.html", {"notifications": notifications})


def set_language(request, lang_code):
    from .translations import TRANSLATIONS
    if lang_code in TRANSLATIONS:
        request.session["site_lang"] = lang_code
    return redirect_back(request, "home")


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------

def _get_cart_items(request):
    """Reads the session cart {product_id: qty} and returns (items, total, count).
    Unavailable products and invalid quantities are dropped."""
    cart = request.session.get("cart", {})
    ids = [pid for pid in cart if str(pid).isdigit()]
    products = Product.objects.select_related("seller_account").in_bulk([int(pid) for pid in ids])
    items = []
    total = Decimal("0")
    count = 0
    for pid, qty in cart.items():
        product = products.get(int(pid)) if str(pid).isdigit() else None
        if not product or not product.is_live:
            continue
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        subtotal = product.price * qty
        total += subtotal
        count += qty
        items.append({"product": product, "qty": qty, "subtotal": subtotal})
    return items, total, count


def cart_count(request):
    from .context_processors import cart_count as _count
    return _count(request)["cart_count"]


def _is_ajax(request):
    return (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
    )


@require_POST
def add_to_cart(request, pk):
    product = get_object_or_404(Product.objects.live(), pk=pk)

    if product.stock <= 0:
        msg = f"{product.name} is out of stock."
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": msg}, status=400)
        messages.error(request, msg)
        return redirect_back(request, "cart")

    cart = request.session.get("cart", {})
    key = str(pk)
    try:
        qty = int(request.POST.get("quantity", 1))
    except (TypeError, ValueError):
        qty = 1
    qty = max(1, min(qty, 99))
    new_qty = cart.get(key, 0) + qty
    warning = None
    if new_qty > product.stock:
        new_qty = product.stock
        warning = f"Only {product.stock} of {product.name} left in stock."
    cart[key] = new_qty
    request.session["cart"] = cart
    request.session.modified = True

    if _is_ajax(request):
        return JsonResponse({
            "ok": True,
            "message": f"{product.name} added to cart.",
            "warning": warning,
            "cart_count": cart_count(request),
            "product_id": product.id,
            "product_qty": new_qty,
        })

    if warning:
        messages.warning(request, warning)
    messages.success(request, f"{product.name} added to cart.")
    return redirect_back(request, "cart")


@require_POST
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
            "cart_total_display": money(total),
            "removed": row is None,
            "product_id": pk,
            "product_qty": row["qty"] if row else 0,
            "product_subtotal": str(row["subtotal"]) if row else "0",
            "product_subtotal_display": money(row["subtotal"]) if row else money(0),
        })
    return redirect("cart")


def cart_view(request):
    items, total, count = _get_cart_items(request)
    return render(request, "bees/cart.html", {
        "items": items,
        "total": total,
        "count": count,
    })


@require_POST
def cart_bulk_remove(request):
    cart = request.session.get("cart", {})
    for pid in request.POST.getlist("selected"):
        cart.pop(pid, None)
    request.session["cart"] = cart
    request.session.modified = True
    messages.success(request, "Selected items removed from cart.")
    return redirect("cart")


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
    return redirect_back(request, "home")


def compare_page(request):
    compare_ids = request.session.get("compare", [])
    products = with_ratings(Product.objects.live().filter(id__in=compare_ids))
    return render(request, "bees/compare.html", {"products": products})


@login_required
def toggle_wishlist(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        item, created = Wishlist.objects.get_or_create(user=request.user, product=product)
        if not created:
            item.delete()
            messages.info(request, f"Removed {product.name} from your wishlist.")
        else:
            messages.success(request, f"Added {product.name} to your wishlist.")
    return redirect_back(request, "home")


@login_required
def wishlist_view(request):
    items = Wishlist.objects.filter(user=request.user).select_related("product")
    return render(request, "bees/wishlist.html", {"items": items})


# ---------------------------------------------------------------------------
# Checkout & orders
# ---------------------------------------------------------------------------

def _payment_methods():
    methods = []
    if payments.is_configured():
        methods.append("card")
    if _brand().allow_cash_on_delivery:
        methods.append("cod")
    return methods


def _price_cart(subtotal, coupon=None):
    """Returns the full price breakdown for a cart subtotal."""
    brand = _brand()
    discount = Decimal("0")
    if coupon:
        discount = (subtotal * Decimal(coupon.percent_off) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    discounted = max(subtotal - discount, Decimal("0"))
    # Free-shipping threshold is checked against what the customer actually
    # pays for the goods (after the coupon).
    shipping = brand.shipping_for(discounted) if subtotal else Decimal("0")
    tax = (discounted * Decimal(brand.tax_percent or 0) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "subtotal": subtotal,
        "discount": discount,
        "shipping": shipping,
        "tax": tax,
        "total": discounted + shipping + tax,
        "tax_percent": brand.tax_percent,
    }


def _session_coupon(request, subtotal):
    code = request.session.get("coupon_code", "")
    if not code:
        return None, None
    coupon = Coupon.objects.filter(code__iexact=code).first()
    if not coupon:
        return None, "That coupon code no longer exists."
    is_valid, error = coupon.is_valid_for(request.user, subtotal)
    return (coupon, None) if is_valid else (None, error)


CHECKOUT_REQUIRED = ("full_name", "email", "phone", "address", "city", "postal_code", "country")


class CheckoutError(Exception):
    pass


@login_required
@ratelimit("checkout", rate_limit=10, window_seconds=300, redirect_to="cart",
           message="Too many checkout attempts. Please wait a few minutes and try again.")
def checkout_view(request):
    items, subtotal, count = _get_cart_items(request)
    if not items:
        messages.error(request, "Your cart is empty.")
        return redirect("cart")

    coupon, coupon_error = _session_coupon(request, subtotal)
    pricing = _price_cart(subtotal, coupon)
    methods = _payment_methods()

    if request.method == "POST":
        if coupon_error:
            request.session["coupon_code"] = ""
            messages.error(request, coupon_error)
            return redirect("checkout")
        payment_method = request.POST.get("payment_method") or (methods[0] if methods else "")
        if payment_method not in methods:
            messages.error(request, "Please choose an available payment method.")
            return redirect("checkout")

        data = {f: request.POST.get(f, "").strip() for f in CHECKOUT_REQUIRED + ("state",)}
        data["country"] = data["country"].upper()[:2]
        missing = [f.replace("_", " ") for f in CHECKOUT_REQUIRED if not data[f]]
        try:
            if missing:
                raise ValidationError(f"Please fill in: {', '.join(missing)}.")
            validate_email(data["email"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return render(request, "bees/checkout.html", _checkout_context(request, items, pricing, coupon, methods, data))

        try:
            order = _place_order(request, items, data, payment_method, use_credit=request.POST.get("use_credit") == "1")
        except CheckoutError as exc:
            messages.error(request, str(exc))
            return redirect("cart")

        request.session["cart"] = {}
        request.session["coupon_code"] = ""
        request.session.modified = True

        if payment_method == "card" and order.total_cents > 0:
            try:
                return redirect(payments.create_checkout_session(request, order))
            except payments.PaymentError as exc:
                payments.release_unpaid_order(order.id)
                _restore_cart_from_order(request, order)
                messages.error(request, str(exc))
                return redirect("cart")

        if order.total_cents == 0:  # covered by coupon / store credit, nothing to charge
            order.payment_status = "paid"
            order.status = "confirmed"
            order._skip_status_email = True  # the confirmation email below covers it
            order.save(update_fields=["payment_status", "status"])

        _send_order_confirmation(request, order)
        Notification.objects.create(user=request.user, message=f"Order #{order.id} placed successfully.", link="/my-orders/")
        return redirect("order_success", order_id=order.id)

    return render(request, "bees/checkout.html", _checkout_context(request, items, pricing, coupon, methods))


def _store_credit(user):
    profile = Profile.objects.filter(user=user).only("store_credit").first()
    return profile.store_credit if profile else Decimal("0")


def _checkout_context(request, items, pricing, coupon, methods, form=None):
    if form is None:
        form = {"email": request.user.email, "full_name": request.user.get_full_name()}
        default = Address.objects.filter(user=request.user, is_default=True).first()
        if default:
            form.update({f: getattr(default, f) for f in ("full_name", "phone", "address", "city", "state", "postal_code", "country")})
    credit = _store_credit(request.user)
    credit_applicable = min(credit, pricing["total"])
    return {
        "store_credit": credit,
        "credit_applicable": credit_applicable,
        "total_after_credit": pricing["total"] - credit_applicable,
        "items": items,
        "pricing": pricing,
        "total": pricing["subtotal"],
        "discount_amount": pricing["discount"],
        "final_total": pricing["total"],
        "coupon": coupon,
        "addresses": Address.objects.filter(user=request.user),
        "payment_methods": methods,
        "form": form,
    }


def _place_order(request, items, data, payment_method, use_credit=False):
    """Creates the order atomically: locks the product rows so two buyers
    can't both take the last unit, re-checks stock and the coupon inside
    the transaction, then decrements stock."""
    with transaction.atomic():
        product_ids = sorted(item["product"].id for item in items)
        locked = {p.id: p for p in Product.objects.select_for_update().filter(id__in=product_ids).order_by("id")}

        subtotal = Decimal("0")
        lines = []
        for item in items:
            product = locked.get(item["product"].id)
            if not product or not product.is_live:
                raise CheckoutError(f"{item['product'].name} is no longer available.")
            if item["qty"] > product.stock:
                raise CheckoutError(f"Sorry, only {product.stock} of {product.name} left in stock. Please update your cart.")
            subtotal += product.price * item["qty"]
            lines.append((product, item["qty"]))

        coupon = None
        code = request.session.get("coupon_code", "")
        if code:
            coupon = Coupon.objects.select_for_update().filter(code__iexact=code).first()
            if coupon:
                is_valid, error = coupon.is_valid_for(request.user, subtotal)
                if not is_valid:
                    raise CheckoutError(error)

        pricing = _price_cart(subtotal, coupon)
        is_card = payment_method == "card"
        order = Order.objects.create(
            user=request.user,
            email=data["email"],
            full_name=data["full_name"][:150],
            address=data["address"][:255],
            city=data["city"][:100],
            state=data.get("state", "")[:100],
            postal_code=data["postal_code"][:20],
            country=data["country"],
            phone=data["phone"][:30],
            payment_method=payment_method,
            payment_status="pending" if is_card else "not_applicable",
            currency=settings.STORE_CURRENCY,
            coupon_code=coupon.code if coupon else "",
            discount_amount=pricing["discount"],
            shipping_amount=pricing["shipping"],
            tax_amount=pricing["tax"],
            estimated_delivery=order_emails.default_delivery_date(),
        )
        seller_cache = {}
        for product, qty in lines:
            item = OrderItem(order=order, product=product, product_name=product.name, price=product.price, quantity=qty)
            seller = None
            if product.seller_account_id:
                if product.seller_account_id not in seller_cache:
                    seller_cache[product.seller_account_id] = product.seller_account
                seller = seller_cache[product.seller_account_id]
            item.apply_commission(seller)
            item.save()
            product.stock -= qty
            product.save(update_fields=["stock"])  # save() sends low-stock alerts
        if use_credit:
            profile = Profile.objects.select_for_update().filter(user=request.user).first()
            if profile and profile.store_credit > 0:
                credit = min(profile.store_credit, order.grand_total)
                profile.store_credit -= credit
                profile.save(update_fields=["store_credit"])
                order.credit_used = credit
                order.save(update_fields=["credit_used"])
    return order


def _restore_cart_from_order(request, order):
    cart = request.session.get("cart", {})
    for item in order.items.all():
        if item.product_id:
            cart[str(item.product_id)] = cart.get(str(item.product_id), 0) + item.quantity
    request.session["cart"] = cart
    if order.coupon_code and not request.session.get("coupon_code"):
        request.session["coupon_code"] = order.coupon_code
    request.session.modified = True


@require_POST
def apply_coupon(request):
    code = request.POST.get("coupon_code", "").strip()[:30]
    request.session["coupon_code"] = code
    request.session.modified = True
    if code:
        coupon = Coupon.objects.filter(code__iexact=code).first()
        if not coupon:
            request.session["coupon_code"] = ""
            messages.error(request, "That coupon code isn't valid.")
        else:
            _, total, _ = _get_cart_items(request)
            is_valid, error = coupon.is_valid_for(request.user, total)
            if not is_valid:
                request.session["coupon_code"] = ""
                messages.error(request, error)
            else:
                messages.success(request, f"Coupon applied: {coupon.percent_off}% off.")
    return redirect("checkout")


def _get_order_for_viewer(request, order_id):
    """Orders are visible to their owner and to staff only."""
    if not request.user.is_authenticated:
        raise Http404
    if request.user.is_staff:
        return get_object_or_404(Order, pk=order_id)
    return get_object_or_404(Order, pk=order_id, user=request.user)


@login_required
def order_success(request, order_id):
    order = _get_order_for_viewer(request, order_id)
    return render(request, "bees/order_success.html", {"order": order})


@login_required
def my_orders(request):
    orders = Order.objects.filter(user=request.user).prefetch_related(
        "items", "items__product", "items__return_requests"
    ).order_by("-created_at")
    return render(request, "bees/my_orders.html", {"orders": orders})


@login_required
@require_POST
def cancel_order(request, pk):
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=pk, user=request.user)
        if not order.is_cancellable:
            messages.error(request, "This order can no longer be cancelled.")
            return redirect("my_orders")
        if order.payment_status == "paid":
            if not payments.refund_order(order):
                messages.error(request, "We couldn't process the refund automatically. Please contact support and we'll sort it out.")
                return redirect("my_orders")
            order.payment_status = "refunded"
        payments.void_pending_payment(order)
        order.status = "cancelled"
        order.save(update_fields=["status", "payment_status", "payment_method", "cod_fallback"])
        payments.restock(order)
    if order.payment_status == "refunded":
        messages.success(request, f"Order #{order.id} has been cancelled and a full refund issued to your card.")
    else:
        messages.success(request, f"Order #{order.id} has been cancelled.")
    return redirect("my_orders")


@login_required
def request_return(request, item_id):
    item = get_object_or_404(OrderItem, pk=item_id, order__user=request.user)
    if item.order.status != "delivered":
        messages.error(request, "Returns can only be requested for delivered orders.")
        return redirect("my_orders")
    if not item.order.can_return:
        messages.error(request, f"The {_brand().return_days}-day return window for order #{item.order_id} has closed. Please contact support if something is wrong.")
        return redirect("my_orders")
    if item.return_requests.exclude(status="rejected").exists():
        messages.error(request, "A return request already exists for this item.")
        return redirect("my_orders")

    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()[:2000]
        refund_method = request.POST.get("refund_method", "original_payment")
        if refund_method not in dict(ReturnRequest.REFUND_METHOD_CHOICES):
            refund_method = "original_payment"
        if not reason:
            messages.error(request, "Please describe the reason for your return.")
        else:
            ReturnRequest.objects.create(
                order_item=item, user=request.user, reason=reason, refund_method=refund_method,
            )
            messages.success(request, "Return request submitted. We'll review it shortly.")
            return redirect("my_orders")

    order = item.order
    return render(request, "bees/request_return.html", {
        "item": item,
        "refund_value": order.refund_value(item),
        "card_order": order.payment_status == "paid" and bool(order.stripe_payment_intent),
        "deadline": order.return_deadline(),
    })


@login_required
@require_POST
def buy_again(request, order_id):
    order = get_object_or_404(Order, pk=order_id, user=request.user)
    cart = request.session.get("cart", {})
    added, skipped = 0, 0
    for item in order.items.select_related("product"):
        if not item.product or item.product.stock <= 0 or not item.product.is_live:
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
def invoice_pdf(request, order_id):
    order = _get_order_for_viewer(request, order_id)

    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    brand = _brand()
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    def hex_to_rgb(value, fallback=(0.07, 0.09, 0.15)):
        try:
            value = value.lstrip("#")
            return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
        except Exception:
            return fallback

    p.setFillColorRGB(*hex_to_rgb(brand.primary_color))
    p.rect(0, height - 64, width, 64, fill=1, stroke=0)
    p.setFillColorRGB(1, 1, 1)
    p.setFont("Helvetica-Bold", 20)
    p.drawString(40, height - 42, brand.site_name[:40])
    p.setFont("Helvetica", 10)
    p.drawRightString(width - 40, height - 40, "INVOICE")

    p.setFillColorRGB(0, 0, 0)
    p.setFont("Helvetica-Bold", 14)
    p.drawString(40, height - 96, f"Order #{order.id}")
    p.setFont("Helvetica", 10)
    lines = [
        f"Date: {order.created_at.strftime('%d %b %Y')}",
        f"Status: {order.get_status_display()}   Payment: {order.get_payment_status_display()}",
        f"Bill to: {order.full_name}",
        f"{order.address}, {order.city} {order.state} {order.postal_code} {order.country}".strip(),
        f"Phone: {order.phone}   Email: {order.contact_email}",
    ]
    y = height - 116
    for line in lines:
        p.drawString(40, y, line[:110])
        y -= 15

    y -= 20
    p.setFont("Helvetica-Bold", 10)
    p.drawString(40, y, "Item")
    p.drawString(320, y, "Qty")
    p.drawString(370, y, "Price")
    p.drawString(460, y, "Subtotal")
    y -= 16
    p.setFont("Helvetica", 10)
    for item in order.items.all():
        p.drawString(40, y, item.product_name[:45])
        p.drawString(320, y, str(item.quantity))
        p.drawString(370, y, money(item.price))
        p.drawString(460, y, money(item.subtotal))
        y -= 16
        if y < 120:
            p.showPage()
            y = height - 60

    y -= 6
    p.line(40, y, width - 40, y)
    y -= 18
    summary = [("Subtotal", order.subtotal)]
    if order.discount_amount:
        summary.append(("Discount", -order.discount_amount))
    summary.append(("Shipping", order.shipping_amount))
    if order.tax_amount:
        summary.append(("Tax", order.tax_amount))
    if order.credit_used:
        summary.append(("Store credit", -order.credit_used))
    for label, value in summary:
        p.drawString(370, y, f"{label}:")
        p.drawString(460, y, money(value))
        y -= 15
    p.setFont("Helvetica-Bold", 12)
    p.drawString(370, y - 4, "Total:")
    p.drawString(460, y - 4, money(order.total))

    if brand.support_email or brand.company_address:
        p.setFont("Helvetica", 8)
        p.setFillColorRGB(0.4, 0.4, 0.4)
        p.drawString(40, 40, " · ".join(filter(None, [brand.company_address, brand.support_email]))[:140])

    p.showPage()
    p.save()
    buffer.seek(0)
    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="invoice-{order.id}.pdf"'
    return response


# ---------------------------------------------------------------------------
# Stripe payments
# ---------------------------------------------------------------------------

@login_required
def payment_success(request):
    session_id = request.GET.get("session_id", "")
    session = payments.retrieve_session(session_id) if session_id and payments.is_configured() else None
    if not session:
        messages.info(request, "We're confirming your payment. You'll see it in My orders shortly.")
        return redirect("my_orders")
    order_id = (session.get("metadata") or {}).get("order_id")
    order = Order.objects.filter(pk=order_id, user=request.user).first() if order_id else None
    if not order:
        raise Http404
    order, newly_paid = payments.mark_order_paid(order.id, session)
    if newly_paid:
        _send_order_confirmation(request, order)
        Notification.objects.create(user=order.user, message=f"Payment received for order #{order.id}.", link="/my-orders/")
    if order.payment_status == "paid":
        messages.success(request, "Payment received - thank you!")
    else:
        messages.info(request, "Your payment is processing. We'll email you as soon as it's confirmed.")
    return redirect("order_success", order_id=order.id)


@login_required
@require_POST
def pay_online(request, order_id):
    """Lets a customer pay a cash-on-delivery order online in advance."""
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id, user=request.user)
        if not payments.is_configured():
            messages.error(request, "Online payment isn't available right now. You can pay cash on delivery.")
            return redirect("my_orders")
        if order.payment_method != "cod" or order.payment_status != "not_applicable" or order.status not in ("pending", "confirmed"):
            messages.info(request, "This order can't be paid online.")
            return redirect("my_orders")
        order.cod_fallback = True
        order.payment_method = "card"
        order.payment_status = "pending"
        order.save(update_fields=["cod_fallback", "payment_method", "payment_status"])
    try:
        return redirect(payments.create_checkout_session(request, order))
    except payments.PaymentError as exc:
        payments.release_unpaid_order(order.id)
        messages.error(request, str(exc))
        return redirect("my_orders")


@login_required
def payment_cancel(request, order_id):
    order = get_object_or_404(Order, pk=order_id, user=request.user)
    if order.payment_status == "pending" and order.stripe_session_id and payments.is_configured():
        session = payments.retrieve_session(order.stripe_session_id)
        if session and session.get("payment_status") == "paid":
            return redirect(f"{reverse('payment_success')}?session_id={order.stripe_session_id}")
        try:
            payments._stripe().checkout.Session.expire(order.stripe_session_id)
        except Exception:
            pass
    if order.payment_status == "pending":
        was_cod = order.cod_fallback
        payments.release_unpaid_order(order.id, reason="failed")
        if was_cod:
            messages.info(request, "Online payment cancelled - nothing was charged. Your order stays on cash on delivery.")
            return redirect("my_orders")
        _restore_cart_from_order(request, order)
        messages.info(request, "Payment cancelled - nothing was charged. Your items are back in your cart.")
        return redirect("cart")
    return redirect("my_orders")


@login_required
def resume_payment(request, order_id):
    order = get_object_or_404(Order, pk=order_id, user=request.user)
    if order.payment_status != "pending" or not order.stripe_session_id:
        return redirect("my_orders")
    session = payments.retrieve_session(order.stripe_session_id)
    if session and session.get("status") == "open" and session.get("url"):
        return redirect(session["url"])
    if session and session.get("payment_status") == "paid":
        return redirect(f"{reverse('payment_success')}?session_id={order.stripe_session_id}")
    messages.error(request, "That payment link has expired. Please place the order again.")
    return redirect("my_orders")


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Stripe calls this for payment events. Signature-verified, idempotent."""
    if not (payments.is_configured() and payments.webhook_configured()):
        return HttpResponse(status=503)
    try:
        event = payments.parse_webhook(request.body, request.META.get("HTTP_STRIPE_SIGNATURE", ""))
    except ValueError:
        return HttpResponse(status=400)

    event_type = event.get("type", "")
    session = (event.get("data") or {}).get("object") or {}
    order_id = (session.get("metadata") or {}).get("order_id")
    if not order_id:
        return HttpResponse(status=200)

    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        order, newly_paid = payments.mark_order_paid(order_id, session)
        if order and newly_paid:
            _send_order_confirmation(request, order)
            if order.user:
                Notification.objects.create(user=order.user, message=f"Payment received for order #{order.id}.", link="/my-orders/")
    elif event_type in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        order = Order.objects.filter(pk=order_id).first()
        if order and order.stripe_session_id == session.get("id"):
            payments.release_unpaid_order(order.id, reason="failed")
    return HttpResponse(status=200)


# ---------------------------------------------------------------------------
# Content pages
# ---------------------------------------------------------------------------

def help_support(request):
    name = _store_name()
    brand = _brand()
    pay = "secure card payments (Visa, Mastercard, American Express, Apple Pay and Google Pay)"
    if brand.allow_cash_on_delivery:
        pay += " and cash on delivery where available"
    faqs = [
        ("How do I place an order?", "Add products to your cart, open your cart and choose 'Checkout'. Enter your shipping details, pick a payment method and confirm."),
        ("Which payment methods do you accept?", f"We accept {pay}. Card payments are processed by Stripe - we never see or store your card number."),
        ("How can I track my order?", "Sign in and open 'My orders' to see every order, its status and tracking details once it ships."),
        ("Can I return a product?", "Yes. Open the order in 'My orders' and choose 'Request return' within 14 days of delivery. Our team reviews requests within 1-2 business days."),
        ("Can I cancel an order?", "You can cancel from 'My orders' until it ships. Paid orders are refunded to your card automatically."),
        ("I forgot my password - what now?", "Choose 'Forgot password?' on the sign-in page and we'll email you a secure reset link."),
    ]
    return render(request, "bees/help.html", {"faqs": faqs, "store_name": name})


def sell_on_bees(request):
    name = _store_name()
    return render(request, "bees/static_page.html", {
        "page_title": f"Sell on {name}",
        "sections": [
            ("Reach more customers", f"Open your shop on {name} and put your products in front of shoppers browsing every category."),
            ("Simple, transparent fees", "Individual sellers pay a 10% commission per sale and registered businesses 20%. Your rate drops automatically as your sales grow."),
            ("Everything in one dashboard", "Get approved, list products, fulfil orders, invite your team and track earnings from your seller dashboard."),
        ],
        "cta_url": reverse("become_seller"),
        "cta_label": "Apply to become a seller",
    })


def about_us(request):
    name = _store_name()
    return render(request, "bees/static_page.html", {
        "page_title": f"About {name}",
        "sections": [
            ("Who we are", f"{name} is an online marketplace connecting customers with trusted brands and independent sellers across beauty, electronics, fashion, home and more."),
            ("Our promise", "Carefully reviewed sellers, secure checkout, honest reviews from verified buyers, and support that actually answers."),
        ],
    })


def terms_page(request):
    name = _store_name()
    return render(request, "bees/static_page.html", {
        "page_title": "Terms of Service",
        "sections": [
            (f"Using {name}", f"By creating an account or placing an order on {name}, you agree to these terms. Please use the platform responsibly and in line with applicable laws."),
            ("Accounts", "You're responsible for keeping your account credentials secure. Seller accounts are reviewed and approved before going live."),
            ("Orders and payments", "An order is confirmed once payment is received (or, for cash on delivery, once it's placed). Prices and availability may change without notice. Card payments are processed securely by Stripe."),
            ("Returns and refunds", "Eligible items can be returned within 14 days of delivery. Approved refunds go back to the original payment method."),
        ],
    })


def privacy_page(request):
    name = _store_name()
    return render(request, "bees/static_page.html", {
        "page_title": "Privacy Policy",
        "sections": [
            ("What we collect", "Information you give us when you create an account, place an order or apply to sell - such as your name, email, shipping address and phone number. Card details are entered directly with Stripe and never reach our servers."),
            ("How we use it", f"To process and deliver orders, provide support, prevent fraud and improve {name}. We never sell your personal data."),
            ("Your rights", "You can view and update your details from your profile at any time, or contact us to request a copy or deletion of your data."),
        ],
    })


@require_POST
@ratelimit("newsletter", rate_limit=5, window_seconds=600, redirect_to="home")
def newsletter_subscribe(request):
    email = request.POST.get("email", "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        messages.error(request, "Please enter a valid email address.")
        return redirect_back(request, "home")
    NewsletterSubscriber.objects.get_or_create(email=email)
    messages.success(request, "Thanks for subscribing!")
    return redirect_back(request, "home")


# ---------------------------------------------------------------------------
# Sellers
# ---------------------------------------------------------------------------

@login_required
def become_seller(request):
    existing = SellerAccount.objects.filter(user=request.user).first()
    if existing:
        return redirect("seller_dashboard")

    if request.method == "POST":
        account_type = request.POST.get("account_type", "individual")
        if account_type not in dict(SellerAccount.ACCOUNT_TYPE_CHOICES):
            account_type = "individual"
        SellerAccount.objects.create(
            user=request.user,
            account_type=account_type,
            business_name=request.POST.get("business_name", "")[:150],
            phone=request.POST.get("phone", "")[:30],
            country=request.POST.get("country", "")[:100],
        )
        messages.success(request, "Your seller application has been submitted. We'll review it shortly.")
        return redirect("seller_dashboard")

    return render(request, "bees/become_seller.html")


@login_required
def update_fulfillment_status(request, item_id):
    seller, role = get_seller_account_for_user(request.user)
    if not seller:
        return redirect("seller_dashboard")
    item = get_object_or_404(OrderItem.objects.filter(Q(seller_account=seller) | Q(product__seller_account=seller)), pk=item_id)
    new_status = request.POST.get("fulfillment_status")
    if request.method == "POST" and new_status in dict(OrderItem.FULFILLMENT_CHOICES):
        if item.order.status == "cancelled":
            messages.error(request, f"Order #{item.order_id} was cancelled - don't ship '{item.product_name}'.")
            return redirect("seller_dashboard")
        if item.order.payment_status in ("pending", "failed"):
            messages.error(request, f"Order #{item.order_id} hasn't been paid yet - wait before shipping.")
            return redirect("seller_dashboard")
        item.fulfillment_status = new_status
        item.save(update_fields=["fulfillment_status"])
        moved = sync_order_status_from_items(item.order)
        note = f" Order #{item.order_id} is now {moved} and the customer has been emailed." if moved else ""
        messages.success(request, f"Marked '{item.product_name}' as {item.get_fulfillment_status_display()}.{note}")
    return redirect("seller_dashboard")


def sync_order_status_from_items(order):
    """Moves the order forward once every item has reached the next step
    (one order can contain items from several sellers): all handed to the
    courier -> shipped, all delivered -> delivered. Returns the new status
    label, or None if nothing changed."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if order.status in ("cancelled", "delivered"):
            return None
        states = set(order.items.values_list("fulfillment_status", flat=True))
        if not states:
            return None
        if states == {"delivered"}:
            order.status = "delivered"
        elif states <= {"handed_to_courier", "delivered"} and order.status in ("pending", "confirmed"):
            order.status = "shipped"
        else:
            return None
        if not order.estimated_delivery:
            order.estimated_delivery = order_emails.default_delivery_date(order)
        order.save(update_fields=["status", "estimated_delivery"])
    return order.get_status_display().lower()


@login_required
def seller_dashboard(request):
    seller, role = get_seller_account_for_user(request.user)
    if not seller:
        raise Http404("No seller account found for this user.")
    products = Product.objects.filter(seller_account=seller)

    order_items_qs = OrderItem.objects.filter(seller_account=seller).select_related(
        "order", "product"
    ).order_by("-order__created_at")
    order_items = list(order_items_qs)
    counted_ids = set(OrderItem.objects.filter(seller_account=seller).counted().values_list("id", flat=True))
    counted_items = [i for i in order_items if i.id in counted_ids]
    for i in order_items:
        i.counts = i.id in counted_ids
    total_sales = seller.lifetime_sales
    commission_owed = seller.commission_total
    net_earnings = seller.net_earnings

    from datetime import timedelta
    from django.utils import timezone
    today = timezone.localdate()
    daily_sales = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_total = sum(
            it.subtotal for it in counted_items if it.order.created_at.date() == day
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
        "role": role,
        "products": products,
        "order_items": order_items[:30],
        "amount_owed": seller.amount_owed,
        "current_rate": seller.effective_commission_rate,
        "total_sales": total_sales,
        "commission_owed": commission_owed,
        "net_earnings": net_earnings,
        "product_count": products.count(),
        "daily_sales": daily_sales,
        "top_products": top_products,
        "low_stock_products": low_stock_products,
        "out_of_stock_products": out_of_stock_products,
        "open_questions": Question.objects.filter(product__seller_account=seller, answer="").select_related("product").order_by("created_at")[:20],
    })


def _approved_seller_or_404(user):
    seller, role = get_seller_account_for_user(user)
    if not seller or seller.status != "approved":
        raise Http404("No approved seller account found for this user.")
    return seller, role


def _product_fields_from_post(request):
    """Validates the seller product form. Returns a dict of clean values."""
    name = request.POST.get("name", "").strip()[:255]
    if not name:
        raise ValidationError("Product name is required.")
    category = request.POST.get("category", "")
    if category not in dict(Product.CATEGORY_CHOICES):
        raise ValidationError("Please choose a valid category.")
    price = _parse_decimal(request.POST.get("price"), "Price", minimum=Decimal("0.01"))
    old_price = _parse_decimal(request.POST.get("old_price"), "Original price", required=False)
    if old_price is not None and old_price <= price:
        old_price = None
    discount = _parse_int(request.POST.get("discount_percent"), "Discount", minimum=0, maximum=95)
    stock = _parse_int(request.POST.get("stock"), "Stock", minimum=0, maximum=1_000_000)
    image_url = request.POST.get("image_url", "").strip()[:500]
    if image_url and not (image_url.startswith("https://") or image_url.startswith(settings.MEDIA_URL)):
        raise ValidationError("Image link must start with https://")
    uploaded = request.FILES.get("image_file")
    if uploaded:
        validate_image_upload(uploaded)
        from django.core.files.storage import default_storage
        path = default_storage.save(random_upload_name("products", uploaded), uploaded)
        image_url = default_storage.url(path)
    return {
        "name": name, "category": category, "price": price, "old_price": old_price,
        "discount_percent": discount, "stock": stock, "image_url": image_url,
        "description": request.POST.get("description", "").strip()[:10000],
    }


@login_required
def seller_add_product(request):
    seller, role = _approved_seller_or_404(request.user)
    if request.method == "POST":
        try:
            fields = _product_fields_from_post(request)
            if not fields["image_url"]:
                raise ValidationError("Please add a product image (upload a file or paste an https:// link).")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return render(request, "bees/seller_add_product.html", {
                "categories": Product.CATEGORY_CHOICES, "p": request.POST,
            })
        Product.objects.create(
            **fields,
            seller_account=seller,
            seller_name=seller.display_name,
            approval_status="pending",
        )
        messages.success(request, "Product submitted for review. It will go live once approved by an admin.")
        return redirect("seller_dashboard")
    return render(request, "bees/seller_add_product.html", {
        "categories": Product.CATEGORY_CHOICES, "p": {"stock": 10, "discount_percent": 0},
    })


# Changing any of these sends the product back for moderation; price and
# stock updates go live immediately.
REVIEWED_FIELDS = ("name", "category", "image_url", "description")


@login_required
def seller_edit_product(request, pk):
    seller, role = _approved_seller_or_404(request.user)
    product = get_object_or_404(Product, pk=pk, seller_account=seller)
    if request.method == "POST":
        try:
            fields = _product_fields_from_post(request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return render(request, "bees/seller_add_product.html", {
                "categories": Product.CATEGORY_CHOICES, "product": product, "p": request.POST,
            })
        if not fields["image_url"]:
            fields["image_url"] = product.image_url
        needs_review = any(getattr(product, f) != fields[f] for f in REVIEWED_FIELDS)
        for key, value in fields.items():
            setattr(product, key, value)
        if needs_review and product.approval_status != "pending":
            product.approval_status = "pending"
            messages.success(request, "Product updated. Because the listing details changed, it will go live again after a quick review.")
        else:
            messages.success(request, "Product updated.")
        product.save()
        return redirect("seller_dashboard")
    return render(request, "bees/seller_add_product.html", {
        "categories": Product.CATEGORY_CHOICES,
        "product": product, "p": product,
    })


@login_required
@require_POST
def seller_delete_product(request, pk):
    seller, role = _approved_seller_or_404(request.user)
    Product.objects.filter(pk=pk, seller_account=seller).delete()
    messages.success(request, "Product removed from your store.")
    return redirect("seller_dashboard")


@login_required
@require_POST
def add_team_member(request):
    seller, role = get_seller_account_for_user(request.user)
    if not seller or role not in ("owner", "admin"):
        messages.error(request, "Only the account owner or a team admin can manage team members.")
        return redirect("seller_dashboard")
    if seller.account_type != "organization":
        messages.error(request, "Team members are only available for organization accounts.")
        return redirect("seller_dashboard")
    identifier = request.POST.get("username_or_email", "").strip()
    member_role = request.POST.get("role", "staff")
    if member_role not in dict(OrganizationMember.ROLE_CHOICES):
        member_role = "staff"
    user = User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier)).first() if identifier else None
    if not user:
        messages.error(request, f"No user found with username/email '{identifier}'. They need to create an account first.")
    elif user == seller.user:
        messages.error(request, "That's already the account owner.")
    elif OrganizationMember.objects.filter(organization=seller, user=user).exists():
        messages.error(request, f"{user.username} is already on the team.")
    elif SellerAccount.objects.filter(user=user).exists() or OrganizationMember.objects.filter(user=user).exists():
        messages.error(request, f"{user.username} already sells or works with another store. One account can only belong to one store.")
    else:
        OrganizationMember.objects.create(organization=seller, user=user, role=member_role)
        Notification.objects.create(
            user=user,
            message=f"You've been added to {seller.display_name}'s team. You can now help manage their products and orders.",
            link="/seller/dashboard/",
        )
        messages.success(request, f"{user.username} added to the team.")
    return redirect("seller_dashboard")


def notify_question_answered(question):
    if question.user_id and question.answer:
        Notification.objects.create(
            user_id=question.user_id, link=f"/product/{question.product_id}/#questions",
            message=f"Your question about '{question.product.name[:120]}' has been answered.",
        )


@login_required
@require_POST
def seller_answer_question(request, pk):
    seller, role = _approved_seller_or_404(request.user)
    question = get_object_or_404(Question.objects.select_related("product"), pk=pk, product__seller_account=seller)
    answer = request.POST.get("answer", "").strip()[:2000]
    if not answer:
        messages.error(request, "Please write an answer.")
    else:
        had_answer = bool(question.answer)
        question.answer = answer
        question.save(update_fields=["answer"])
        if not had_answer:
            notify_question_answered(question)
        messages.success(request, "Answer published on the product page.")
    return redirect(reverse("seller_dashboard") + "#questions")


@login_required
@require_POST
def remove_team_member(request, member_id):
    seller, role = get_seller_account_for_user(request.user)
    if not seller or role not in ("owner", "admin"):
        messages.error(request, "Only the account owner or a team admin can manage team members.")
        return redirect("seller_dashboard")
    member = get_object_or_404(OrganizationMember, pk=member_id, organization=seller)
    member.delete()
    messages.success(request, "Team member removed.")
    return redirect("seller_dashboard")


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------

def _is_owner(user):
    return user.is_authenticated and user.is_staff


@login_required
def seller_document(request, seller_id, field):
    """Staff-only access to private seller verification documents."""
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff access only.")
    if field not in ("business_certificate", "id_document"):
        raise Http404
    seller = get_object_or_404(SellerAccount, pk=seller_id)
    file = getattr(seller, field)
    if not file:
        raise Http404
    if getattr(settings, "USE_SUPABASE_STORAGE", False):
        return redirect(file.url)  # short-lived signed URL
    from django.http import FileResponse
    return FileResponse(file.open("rb"), as_attachment=False, filename=file.name.rsplit("/", 1)[-1])


@login_required
def owner_dashboard(request):
    """Old URL - the store dashboard now lives in the store admin."""
    return redirect("manage_dashboard")


# ---------------------------------------------------------------------------
# Support chat
# ---------------------------------------------------------------------------

def _get_or_create_chat_thread(request):
    if request.user.is_authenticated:
        guest_id = request.session.pop("guest_chat_thread", None)
        guest = ChatThread.objects.filter(pk=guest_id, user__isnull=True).first() if guest_id else None
        thread = ChatThread.objects.filter(user=request.user).first()
        if guest and not thread:
            # Keep the conversation the visitor started before signing in.
            guest.user = request.user
            guest.save(update_fields=["user"])
            return guest
        if not thread:
            thread = ChatThread.objects.create(user=request.user)
        if guest:
            guest.messages.update(thread=thread)
            guest.delete()
        return thread
    if not request.session.session_key:
        request.session.create()
    thread, _ = ChatThread.objects.get_or_create(
        user=None, session_key=request.session.session_key,
    )
    request.session["guest_chat_thread"] = thread.id
    return thread


def chat_messages(request):
    """Returns this visitor's chat history as JSON, polled by the widget."""
    thread = _get_or_create_chat_thread(request)
    messages_qs = thread.messages.order_by("created_at")
    data = [
        {"sender": m.sender, "message": m.message, "created_at": m.created_at.strftime("%H:%M")}
        for m in messages_qs
    ]
    return JsonResponse({"messages": data})


@ratelimit("chat_send", rate_limit=20, window_seconds=300)
def chat_send(request):
    """Saves a real message from the visitor and stores a simple support
    auto-reply, so the thread is a genuine record staff can review/reply
    to from the admin panel (Chat threads)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    text = request.POST.get("message", "").strip()[:2000]
    if not text:
        return JsonResponse({"error": "Empty message"}, status=400)

    thread = _get_or_create_chat_thread(request)
    ChatMessage.objects.create(thread=thread, sender="user", message=text)

    auto_reply = "Thanks for your message! Our support team typically replies within a few hours."
    if any(w in text.lower() for w in ["order", "track", "delivery", "shipped", "shipping"]):
        auto_reply = "For order status, check My orders in your account, or share your order number here and our team will follow up."
    elif any(w in text.lower() for w in ["refund", "return"]):
        auto_reply = "You can request a return from My orders. Our team reviews return requests within 1-2 business days."

    reply = ChatMessage.objects.create(thread=thread, sender="support", message=auto_reply)
    return JsonResponse({
        "reply": {"sender": reply.sender, "message": reply.message, "created_at": reply.created_at.strftime("%H:%M")},
    })
