"""Customer account settings: password & sign-in, saved cards, addresses,
privacy (email preferences, data download, account deletion) and the
seller's own store settings."""
import json
import secrets
import logging

from django.contrib import messages
from django.contrib.auth import logout as auth_logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import payments
from .models import (
    Address, AuditLog, ChatThread, NewsletterSubscriber, Notification, Order, OrganizationMember,
    Product, Profile, Question, Review, SellerAccount, Wishlist,
)
from .ratelimit import ratelimit
from .security import validate_image_upload

logger = logging.getLogger(__name__)

ADDRESS_FIELDS = ("full_name", "phone", "address", "city", "state", "postal_code", "country")
OPEN_ORDER_STATUSES = ("pending", "confirmed", "shipped")


def _profile(user):
    profile, _ = Profile.objects.get_or_create(user=user, defaults={"referral_code": secrets.token_hex(4).upper()})
    return profile


def _send(subject, template, context, user):
    from .views import _send_html_email
    _send_html_email(subject, template, context, user.email)


def _other_sessions(request):
    """Session keys of this user's other signed-in browsers."""
    keys = []
    current = request.session.session_key
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        if session.session_key == current:
            continue
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if data.get("_auth_user_id") == str(request.user.pk):
            keys.append(session.session_key)
    return keys


# ---------------------------------------------------------------------------
# Security: password and devices
# ---------------------------------------------------------------------------

@login_required
@ratelimit("change_password", rate_limit=10, window_seconds=600, redirect_to="account_security",
           message="Too many attempts. Please wait a few minutes and try again.")
def security(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    form.fields["old_password"].label = "Current password"
    form.fields["new_password2"].label = "Confirm new password"
    for field in form.fields.values():
        field.widget.attrs.update({"class": "input"})
    if request.method == "POST":
        action = request.POST.get("action", "password")
        if action == "signout_others":
            keys = _other_sessions(request)
            Session.objects.filter(session_key__in=keys).delete()
            AuditLog.objects.create(user=request.user, action="Signed out of other devices")
            messages.success(request, f"Signed out of {len(keys)} other device{'s' if len(keys) != 1 else ''}." if keys else "You weren't signed in anywhere else.")
            return redirect("account_security")
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # stay signed in here; other devices are signed out
            AuditLog.objects.create(user=user, action="Changed password")
            if user.email:
                _send("Your password was changed", "bees/emails/security_notice.html", {
                    "user": user, "event": "Your password was changed",
                    "detail": "If this wasn't you, reset your password straight away and contact us.",
                    "reset_url": request.build_absolute_uri(reverse("password_reset")),
                }, user)
            messages.success(request, "Password changed. You've been signed out on your other devices.")
            return redirect("account_security")
    return render(request, "bees/account/security.html", {
        "tab": "security", "form": form, "other_sessions": len(_other_sessions(request)),
    })


# ---------------------------------------------------------------------------
# Saved cards (stored by Stripe, never on our servers)
# ---------------------------------------------------------------------------

@login_required
def payment_methods(request):
    profile = _profile(request.user)
    enabled = payments.is_configured()
    if enabled and request.GET.get("added") and profile.stripe_customer_id:
        if payments.finish_card_setup(request.GET["added"], profile.stripe_customer_id):
            messages.success(request, "Card saved. You can pick it at checkout.")
        return redirect("payment_methods")
    cards, error = [], None
    if enabled and profile.stripe_customer_id:
        try:
            cards = payments.list_cards(profile.stripe_customer_id)
        except payments.PaymentError as exc:
            error = str(exc)
    return render(request, "bees/account/payment_methods.html", {
        "tab": "cards", "enabled": enabled, "cards": cards, "error": error,
    })


@login_required
@require_POST
def add_card(request):
    if not payments.is_configured():
        messages.error(request, "Card payments aren't available yet.")
        return redirect("payment_methods")
    customer_id = payments.ensure_customer(request.user)
    if not customer_id:
        messages.error(request, "We couldn't reach our payment provider. Please try again shortly.")
        return redirect("payment_methods")
    try:
        return redirect(payments.create_card_setup_session(request, customer_id))
    except payments.PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("payment_methods")


@login_required
@require_POST
def remove_card(request):
    profile = _profile(request.user)
    if payments.remove_card(profile.stripe_customer_id, request.POST.get("card", "")):
        AuditLog.objects.create(user=request.user, action="Removed a saved card")
        messages.success(request, "Card removed.")
    else:
        messages.error(request, "That card couldn't be removed. Please try again.")
    return redirect("payment_methods")


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

def _address_data(request):
    data = {f: request.POST.get(f, "").strip() for f in ADDRESS_FIELDS}
    data["country"] = data["country"].upper()[:2]
    missing = [f.replace("_", " ") for f in ("full_name", "phone", "address", "city", "country") if not data[f]]
    if missing:
        raise ValidationError(f"Please fill in: {', '.join(missing)}.")
    limits = {"full_name": 150, "phone": 30, "address": 255, "city": 100, "state": 100, "postal_code": 20}
    return {k: (v[:limits[k]] if k in limits else v) for k, v in data.items()}


def _make_default(user, address):
    Address.objects.filter(user=user).exclude(pk=address.pk).update(is_default=False)
    if not address.is_default:
        address.is_default = True
        address.save(update_fields=["is_default"])


@login_required
def edit_address(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    if request.method == "POST":
        try:
            data = _address_data(request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return render(request, "bees/account/address_form.html", {"tab": "profile", "address": address, "form": request.POST})
        for key, value in data.items():
            setattr(address, key, value)
        address.label = request.POST.get("label", "").strip()[:30] or address.label
        address.save()
        if request.POST.get("is_default"):
            _make_default(request.user, address)
        messages.success(request, "Address updated.")
        return redirect(reverse("profile") + "#addresses")
    return render(request, "bees/account/address_form.html", {"tab": "profile", "address": address, "form": address})


@login_required
@require_POST
def default_address(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    _make_default(request.user, address)
    messages.success(request, f"'{address.label}' is now your default address. It's filled in for you at checkout.")
    return redirect(reverse("profile") + "#addresses")


# ---------------------------------------------------------------------------
# Privacy: emails, data download, delete account
# ---------------------------------------------------------------------------

def _seller_block(user):
    if SellerAccount.objects.filter(user=user).exists():
        return "You have a seller account. To close it, contact us so we can settle payouts and remove your products first."
    if OrganizationMember.objects.filter(user=user).exists():
        return "You're part of a store's team. Ask the store owner to remove you from the team first."
    return ""


@login_required
def privacy(request):
    email = (request.user.email or "").lower()
    if request.method == "POST" and request.POST.get("action") == "emails":
        wants = bool(request.POST.get("marketing"))
        if wants and email:
            NewsletterSubscriber.objects.get_or_create(email=email)
        else:
            NewsletterSubscriber.objects.filter(email__iexact=email).delete()
        messages.success(request, "Email preferences saved." + ("" if email or not wants else " Add an email address to your profile first."))
        return redirect("account_privacy")
    open_orders = Order.objects.filter(user=request.user, status__in=OPEN_ORDER_STATUSES).count()
    return render(request, "bees/account/privacy.html", {
        "tab": "privacy",
        "subscribed": bool(email) and NewsletterSubscriber.objects.filter(email__iexact=email).exists(),
        "open_orders": open_orders,
        "seller_block": _seller_block(request.user),
    })


@login_required
def download_data(request):
    """Everything we hold about this customer, as JSON (GDPR-style export)."""
    user = request.user
    profile = _profile(user)
    data = {
        "exported_at": timezone.now().isoformat(),
        "account": {
            "username": user.username, "name": user.get_full_name(), "email": user.email,
            "joined": user.date_joined.isoformat(), "last_login": user.last_login.isoformat() if user.last_login else None,
            "phone": profile.phone, "email_verified": profile.email_verified,
            "reward_points": profile.loyalty_points, "store_credit": str(profile.store_credit),
            "referral_code": profile.referral_code,
        },
        "addresses": [
            {"label": a.label, **{f: getattr(a, f) for f in ADDRESS_FIELDS}, "default": a.is_default}
            for a in Address.objects.filter(user=user)
        ],
        "orders": [
            {
                "id": o.id, "date": o.created_at.isoformat(), "status": o.status, "payment": o.payment_status,
                "payment_method": o.payment_method, "total": str(o.total), "currency": o.currency,
                "ship_to": {"name": o.full_name, "address": o.address, "city": o.city, "state": o.state,
                            "postal_code": o.postal_code, "country": o.country, "phone": o.phone},
                "items": [{"product": i.product_name, "quantity": i.quantity, "price": str(i.price)} for i in o.items.all()],
            }
            for o in Order.objects.filter(user=user).prefetch_related("items")
        ],
        "reviews": [{"product": r.product.name, "rating": r.rating, "comment": r.comment, "date": r.created_at.isoformat()}
                    for r in Review.objects.filter(user=user).select_related("product")],
        "questions": [{"product": q.product.name, "question": q.question, "answer": q.answer}
                      for q in Question.objects.filter(user=user).select_related("product")],
        "wishlist": list(Wishlist.objects.filter(user=user).values_list("product__name", flat=True)),
        "newsletter": NewsletterSubscriber.objects.filter(email__iexact=user.email or "-").exists(),
    }
    response = HttpResponse(json.dumps(data, indent=2), content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="my-data-{user.username}.json"'
    AuditLog.objects.create(user=user, action="Downloaded personal data")
    return response


@login_required
@require_POST
@ratelimit("delete_account", rate_limit=5, window_seconds=600, redirect_to="account_privacy")
def delete_account(request):
    user = request.user
    if not user.check_password(request.POST.get("password", "")):
        messages.error(request, "That password isn't right, so your account was not deleted.")
        return redirect("account_privacy")
    blocked = _seller_block(user)
    if user.is_staff:
        blocked = "Staff accounts can't be deleted here."
    if not blocked and Order.objects.filter(user=user, status__in=OPEN_ORDER_STATUSES).exists():
        blocked = "You have orders that haven't been delivered yet. Cancel them or wait until they arrive, then try again."
    if blocked:
        messages.error(request, blocked)
        return redirect("account_privacy")

    profile = _profile(user)
    payments.delete_customer(profile.stripe_customer_id)  # removes saved cards at Stripe
    email = user.email
    with transaction.atomic():
        # Orders stay (stores must keep sales records) but are no longer
        # linked to a person who can sign in.
        Address.objects.filter(user=user).delete()
        Wishlist.objects.filter(user=user).delete()
        Notification.objects.filter(user=user).delete()
        ChatThread.objects.filter(user=user).delete()
        Review.objects.filter(user=user).update(user=None, username="Former customer")
        Question.objects.filter(user=user).update(user=None, username="Former customer")
        if email:
            NewsletterSubscriber.objects.filter(email__iexact=email).delete()
        Profile.objects.filter(pk=profile.pk).update(
            phone="", stripe_customer_id="", store_credit=0, loyalty_points=0, email_verified=False,
        )
        user.username = f"deleted-{user.pk}-{secrets.token_hex(3)}"
        user.email = ""
        user.first_name = ""
        user.last_name = ""
        user.is_active = False
        user.set_unusable_password()
        user.save()
        AuditLog.objects.create(user=user, action="Deleted account")
    auth_logout(request)
    messages.success(request, "Your account has been deleted. We're sorry to see you go.")
    return redirect("home")


# ---------------------------------------------------------------------------
# Seller: store settings
# ---------------------------------------------------------------------------

STORE_TEXT_FIELDS = {
    "store_description": 5000, "phone": 30, "business_address": 255, "city": 100, "country": 100,
    "bank_details": 255, "tax_info": 100, "brand_info": 5000,
}


@login_required
def store_settings(request):
    from .views import get_seller_account_for_user
    seller, role = get_seller_account_for_user(request.user)
    if not seller:
        return redirect("become_seller")
    if role not in ("owner", "admin"):
        messages.error(request, "Only the store owner or a team admin can change store settings.")
        return redirect("seller_dashboard")
    if request.method == "POST":
        name = request.POST.get("store_name", "").strip()[:150]
        try:
            if not name:
                raise ValidationError("Store name is required.")
            taken = SellerAccount.objects.exclude(pk=seller.pk).filter(business_name__iexact=name).exists() or \
                SellerAccount.objects.exclude(pk=seller.pk).filter(organization_name__iexact=name).exists()
            if taken:
                raise ValidationError("Another store already uses that name.")
            for field in ("store_logo", "store_banner"):
                validate_image_upload(request.FILES.get(field))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("seller_store_settings")
        old_name = seller.display_name
        with transaction.atomic():
            if seller.account_type == "organization" and not seller.business_name:
                seller.organization_name = name
            else:
                seller.business_name = name
            for field, limit in STORE_TEXT_FIELDS.items():
                setattr(seller, field, request.POST.get(field, "").strip()[:limit])
            for field in ("store_logo", "store_banner"):
                if request.FILES.get(field):
                    setattr(seller, field, request.FILES[field])
                elif request.POST.get(f"remove_{field}"):
                    setattr(seller, field, None)
            seller.save()
            if seller.display_name != old_name:
                Product.objects.filter(seller_account=seller).update(seller_name=seller.display_name)
        AuditLog.objects.create(user=request.user, action=f"Updated store settings for {seller.display_name}")
        messages.success(request, "Store settings saved.")
        return redirect("seller_store_settings")
    return render(request, "bees/account/store_settings.html", {"seller": seller, "role": role})
