"""
Store admin ("/manage/"): the owner's day-to-day back office, built with the
same design system as the storefront. The full Django admin stays available
at /admin/ for anything not covered here.
"""
import csv
from datetime import date, timedelta
from decimal import Decimal
from functools import wraps
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q, Sum, Exists, OuterRef
from django.db.models.functions import TruncDate
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import payments
from .models import (
    AuditLog, ChatMessage, ChatThread, Coupon, Notification, Order, OrderItem,
    Product, ProductImage, Question, ReturnRequest, Review, SellerAccount, SiteSettings,
)
from .templatetags.bees_extras import money
from .security import safe_next_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def staff_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?{urlencode({'next': request.get_full_path()})}")
        if not request.user.is_staff:
            return HttpResponseForbidden("Store staff only.")
        return view(request, *args, **kwargs)
    return wrapped


def attention_counts(force=False):
    """Numbers shown as badges in the admin sidebar and header icon.
    Cached for 30 seconds so they don't add queries to every page."""
    data = None if force else cache.get("manage:attention")
    if data is None:
        unread_user_msgs = ChatMessage.objects.filter(thread=OuterRef("pk"), sender="user", is_read=False)
        data = {
            "orders": Order.objects.filter(status__in=["pending", "confirmed"]).exclude(payment_status__in=["pending", "failed"]).count(),
            "products": Product.objects.filter(approval_status="pending").count(),
            "sellers": SellerAccount.objects.filter(status="pending").count(),
            "returns": ReturnRequest.objects.filter(status="requested").count(),
            "questions": Question.objects.filter(answer="").count(),
            "messages": ChatThread.objects.filter(is_resolved=False).filter(Exists(unread_user_msgs)).count(),
        }
        data["total"] = data["orders"] + data["products"] + data["sellers"] + data["returns"] + data["messages"]
        cache.set("manage:attention", data, 30)
    return data


def _refresh_attention():
    cache.delete("manage:attention")


def _log(request, action):
    AuditLog.objects.create(user=request.user, action=action[:255])


def _page(request, qs, per_page=25):
    return Paginator(qs, per_page).get_page(request.GET.get("page"))


def _render(request, template, context):
    context.setdefault("attention", attention_counts())
    return render(request, f"bees/manage/{template}", context)


def _back(request, fallback):
    return redirect(safe_next_url(request, request.POST.get("next"), fallback))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@staff_required
def dashboard(request):
    today = timezone.localdate()
    days = 30 if request.GET.get("range") == "30" else 7
    start = today - timedelta(days=days - 1)
    counted = Order.objects.exclude(status="cancelled").exclude(payment_status__in=["pending", "failed"])

    items_by_day = {
        r["day"]: r["v"] or 0
        for r in counted.filter(created_at__date__gte=start).annotate(day=TruncDate("created_at"))
        .values("day").annotate(v=Sum(F("items__price") * F("items__quantity")))
    }
    extra_by_day = {
        r["day"]: (r["extra"] or 0) - (r["disc"] or 0)
        for r in counted.filter(created_at__date__gte=start).annotate(day=TruncDate("created_at"))
        .values("day").annotate(extra=Sum(F("shipping_amount") + F("tax_amount")), disc=Sum("discount_amount"))
    }
    orders_by_day = {
        r["day"]: r["n"]
        for r in counted.filter(created_at__date__gte=start).annotate(day=TruncDate("created_at"))
        .values("day").annotate(n=Count("id"))
    }
    series = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        amount = max(float(items_by_day.get(d, 0)) + float(extra_by_day.get(d, 0)), 0)
        series.append({"date": d, "amount": amount, "orders": orders_by_day.get(d, 0)})
    peak = max([s["amount"] for s in series] or [0]) or 1
    for s in series:
        s["pct"] = round(s["amount"] / peak * 100, 1)
    period_revenue = sum(s["amount"] for s in series)
    period_orders = sum(s["orders"] for s in series)

    prev_start = start - timedelta(days=days)
    prev = counted.filter(created_at__date__gte=prev_start, created_at__date__lt=start)
    prev_items = prev.aggregate(v=Sum(F("items__price") * F("items__quantity")))["v"] or 0
    prev_extra = prev.aggregate(e=Sum(F("shipping_amount") + F("tax_amount")), d=Sum("discount_amount"))
    prev_revenue = max(float(prev_items) + float(prev_extra["e"] or 0) - float(prev_extra["d"] or 0), 0)
    change = None
    if prev_revenue:
        change = round((period_revenue - prev_revenue) / prev_revenue * 100)

    top_products = (
        OrderItem.objects.filter(order__in=counted.filter(created_at__date__gte=start))
        .values("product_name").annotate(units=Sum("quantity"), revenue=Sum(F("price") * F("quantity")))
        .order_by("-revenue")[:5]
    )
    recent_orders = Order.objects.select_related("user").prefetch_related("items").order_by("-created_at")[:8]
    low_stock = Product.objects.filter(stock__lte=5).order_by("stock", "name")[:8]

    return _render(request, "dashboard.html", {
        "section": "dashboard",
        "days": days,
        "series": series,
        "period_revenue": period_revenue,
        "period_orders": period_orders,
        "avg_order": (period_revenue / period_orders) if period_orders else 0,
        "change": change,
        "customers": User.objects.filter(is_staff=False).count(),
        "new_customers": User.objects.filter(is_staff=False, date_joined__date__gte=start).count(),
        "top_products": top_products,
        "recent_orders": recent_orders,
        "low_stock": low_stock,
        "attention": attention_counts(force=True),
    })


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def _filtered_orders(request):
    qs = Order.objects.select_related("user").prefetch_related("items").order_by("-created_at")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    payment = request.GET.get("payment", "")
    if q:
        cond = Q(full_name__icontains=q) | Q(email__icontains=q) | Q(user__email__icontains=q) | Q(user__username__icontains=q) | Q(phone__icontains=q) | Q(tracking_number__icontains=q)
        if q.lstrip("#").isdigit():
            cond |= Q(pk=int(q.lstrip("#")))
        qs = qs.filter(cond)
    if status == "open":
        qs = qs.filter(status__in=["pending", "confirmed"]).exclude(payment_status__in=["pending", "failed"])
    elif status in dict(Order.STATUS_CHOICES):
        qs = qs.filter(status=status)
    if payment in dict(Order.PAYMENT_STATUS_CHOICES):
        qs = qs.filter(payment_status=payment)
    return qs, q, status, payment


@staff_required
def orders(request):
    qs, q, status, payment = _filtered_orders(request)
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="orders.csv"'
        w = csv.writer(response)
        w.writerow(["order", "date", "customer", "email", "country", "status", "payment", "method", "items", "total"])
        for o in qs[:5000]:
            w.writerow([o.id, o.created_at.strftime("%Y-%m-%d %H:%M"), o.full_name, o.contact_email, o.country,
                        o.status, o.payment_status, o.payment_method, sum(i.quantity for i in o.items.all()), f"{o.total:.2f}"])
        return response
    return _render(request, "orders.html", {
        "section": "orders", "page_obj": _page(request, qs), "q": q, "status": status, "payment": payment,
        "status_choices": Order.STATUS_CHOICES, "payment_choices": Order.PAYMENT_STATUS_CHOICES,
    })


def _send_shipped_email(request, order):
    from .views import _send_html_email
    _send_html_email(
        f"Your order #{order.id} is on its way",
        "bees/emails/order_shipped.html",
        {"order": order, "orders_url": request.build_absolute_uri(reverse("my_orders"))},
        order.contact_email,
    )


@staff_required
def order_detail(request, pk):
    order = get_object_or_404(Order.objects.select_related("user"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "cancel" and order.status == "delivered":
            messages.error(request, "Delivered orders can't be cancelled. Refund items from Returns instead.")
        elif action == "cancel":
            from .admin import cancel_order_with_side_effects
            error = cancel_order_with_side_effects(order, request)
            if error:
                messages.error(request, error)
            else:
                _log(request, f"Cancelled order #{order.id} from store admin")
                messages.success(request, f"Order #{order.id} cancelled. Stock returned" + (" and payment refunded." if order.payment_status == "refunded" else "."))
        elif action == "update":
            new_status = request.POST.get("status", order.status)
            if new_status not in dict(Order.STATUS_CHOICES) or new_status == "cancelled":
                new_status = order.status
            was = order.status
            order.tracking_number = request.POST.get("tracking_number", "").strip()[:60]
            order.courier_name = request.POST.get("courier_name", "").strip()[:60]
            eta = request.POST.get("estimated_delivery", "").strip()
            try:
                order.estimated_delivery = date.fromisoformat(eta) if eta else None
            except ValueError:
                messages.error(request, "Estimated delivery must be a valid date.")
                return redirect("manage_order", pk=pk)
            order.status = new_status
            order.save()  # notifies the customer when the status changes
            if new_status != was:
                _log(request, f"Order #{order.id}: {was} -> {new_status}")
                if new_status == "shipped":
                    _send_shipped_email(request, order)
                if new_status == "delivered":
                    order.items.update(fulfillment_status="delivered")
            messages.success(request, "Order updated." + (" The customer has been emailed." if new_status == "shipped" and was != "shipped" else ""))
        _refresh_attention()
        return redirect("manage_order", pk=pk)
    items = order.items.select_related("product", "product__seller_account")
    return _render(request, "order_detail.html", {
        "section": "orders", "order": order, "items": items,
        "status_choices": [c for c in Order.STATUS_CHOICES if c[0] != "cancelled"],
        "returns": ReturnRequest.objects.filter(order_item__order=order).select_related("order_item"),
        "stripe_test": str(getattr(payments.settings, "STRIPE_SECRET_KEY", "")).startswith("sk_test"),
    })


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

@staff_required
def products(request):
    qs = Product.objects.select_related("seller_account").order_by("-id")
    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    state = request.GET.get("state", "")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(seller_name__icontains=q))
    if category in dict(Product.CATEGORY_CHOICES):
        qs = qs.filter(category=category)
    if state in ("pending", "approved", "rejected"):
        qs = qs.filter(approval_status=state)
    elif state == "low":
        qs = qs.filter(stock__gt=0, stock__lte=5)
    elif state == "out":
        qs = qs.filter(stock__lte=0)
    elif state == "deals":
        qs = qs.filter(is_flash_sale=True)
    return _render(request, "products.html", {
        "section": "products", "page_obj": _page(request, qs), "q": q, "category": category, "state": state,
        "categories": Product.CATEGORY_CHOICES,
    })


def _notify_seller(product, message):
    if product.seller_account_id:
        Notification.objects.create(user=product.seller_account.user, message=message[:255], link="/seller/dashboard/")


@staff_required
@require_POST
def products_bulk(request):
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
    action = request.POST.get("action")
    qs = Product.objects.filter(pk__in=ids).select_related("seller_account")
    if not ids:
        messages.warning(request, "Select at least one product first.")
        return _back(request, reverse("manage_products"))
    count = qs.count()
    if action in ("approve", "reject"):
        status = "approved" if action == "approve" else "rejected"
        for p in qs:
            if p.approval_status != status:
                p.approval_status = status
                p.save(update_fields=["approval_status"])
                _notify_seller(p, f"'{p.name}' was {'approved and is now live' if status == 'approved' else 'not approved'}.")
        messages.success(request, f"{count} product(s) {'approved' if status == 'approved' else 'rejected'}.")
    elif action in ("deal_on", "deal_off"):
        qs.update(is_flash_sale=(action == "deal_on"))
        messages.success(request, f"{count} product(s) {'added to' if action == 'deal_on' else 'removed from'} today's deals.")
    elif action == "delete":
        names = ", ".join(qs.values_list("name", flat=True)[:5])
        qs.delete()
        messages.success(request, f"Deleted {count} product(s).")
        _log(request, f"Deleted products: {names}")
    else:
        messages.error(request, "Choose an action.")
    _log(request, f"Bulk {action} on {count} product(s)")
    _refresh_attention()
    return _back(request, reverse("manage_products"))


@staff_required
def product_form(request, pk=None):
    from .views import _product_fields_from_post
    product = get_object_or_404(Product, pk=pk) if pk else None
    if request.method == "POST":
        try:
            fields = _product_fields_from_post(request)
            if not fields["image_url"]:
                if product:
                    fields["image_url"] = product.image_url
                else:
                    raise ValidationError("Please add a product image (upload a file or paste an https:// link).")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return _render(request, "product_form.html", {
                "section": "products", "product": product, "form": request.POST, "categories": Product.CATEGORY_CHOICES,
                "approval_choices": Product.APPROVAL_CHOICES,
            })
        approval = request.POST.get("approval_status", "approved")
        if approval not in dict(Product.APPROVAL_CHOICES):
            approval = "approved"
        is_new = product is None
        old_approval = product.approval_status if product else None
        product = product or Product()
        for key, value in fields.items():
            setattr(product, key, value)
        product.seller_name = (request.POST.get("seller_name", "").strip() or product.seller_name or "Official Store")[:100]
        product.is_flash_sale = bool(request.POST.get("is_flash_sale"))
        product.approval_status = approval
        product.save()
        extra = [u.strip() for u in request.POST.get("extra_images", "").splitlines() if u.strip().startswith("https://")]
        product.extra_images.all().delete()
        ProductImage.objects.bulk_create([ProductImage(product=product, image_url=u[:500]) for u in extra[:8]])
        if old_approval and old_approval != approval and approval in ("approved", "rejected"):
            _notify_seller(product, f"'{product.name}' was {'approved and is now live' if approval == 'approved' else 'not approved'}.")
        _log(request, f"{'Created' if is_new else 'Edited'} product #{product.id} ({product.name})")
        _refresh_attention()
        messages.success(request, "Product saved." if not is_new else "Product created.")
        return redirect("manage_product_edit", pk=product.pk)
    return _render(request, "product_form.html", {
        "section": "products", "product": product, "categories": Product.CATEGORY_CHOICES,
        "approval_choices": Product.APPROVAL_CHOICES,
        "extra_images": "\n".join(product.extra_images.values_list("image_url", flat=True)) if product else "",
    })


# ---------------------------------------------------------------------------
# Sellers
# ---------------------------------------------------------------------------

@staff_required
def sellers(request):
    qs = SellerAccount.objects.select_related("user").annotate(product_count=Count("products")).order_by("-created_at")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if q:
        qs = qs.filter(Q(business_name__icontains=q) | Q(organization_name__icontains=q) | Q(full_name__icontains=q) | Q(user__username__icontains=q) | Q(user__email__icontains=q))
    if status in dict(SellerAccount.STATUS_CHOICES):
        qs = qs.filter(status=status)
    return _render(request, "sellers.html", {
        "section": "sellers", "page_obj": _page(request, qs), "q": q, "status": status, "status_choices": SellerAccount.STATUS_CHOICES,
    })


@staff_required
def seller_detail(request, pk):
    seller = get_object_or_404(SellerAccount.objects.select_related("user"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        note = request.POST.get("note", "").strip()[:255]
        if action in ("approved", "rejected", "suspended"):
            seller.status = action
            if note:
                seller.admin_note = note
            seller.save(update_fields=["status", "admin_note"])
            text = {
                "approved": "Your seller account has been approved! You can now list products.",
                "rejected": "Your seller application was not approved." + (f" {note}" if note else ""),
                "suspended": "Your seller account has been suspended." + (f" {note}" if note else ""),
            }[action]
            Notification.objects.create(user=seller.user, message=text[:255], link="/seller/dashboard/")
            _log(request, f"Seller #{seller.id} ({seller.display_name}) -> {action}")
            messages.success(request, f"{seller.display_name} is now {seller.get_status_display().lower()}.")
        elif action == "commission":
            try:
                rate = Decimal(request.POST.get("commission_rate", ""))
                if not (0 <= rate <= 90):
                    raise ValueError
            except Exception:
                messages.error(request, "Commission must be a number between 0 and 90.")
                return redirect("manage_seller", pk=pk)
            seller.commission_rate = rate
            seller.save(update_fields=["commission_rate"])
            _log(request, f"Seller #{seller.id} commission set to {rate}%")
            messages.success(request, "Commission rate saved.")
        elif action == "payout":
            try:
                amount = Decimal(request.POST.get("amount", "")).quantize(Decimal("0.01"))
                if amount <= 0:
                    raise ValueError
            except Exception:
                messages.error(request, "Enter the payout amount as a positive number.")
                return redirect("manage_seller", pk=pk)
            seller.total_paid_out = (seller.total_paid_out or 0) + amount
            seller.save(update_fields=["total_paid_out"])
            Notification.objects.create(user=seller.user, message=f"A payout of {money(amount)} has been sent to you.", link="/seller/dashboard/")
            _log(request, f"Recorded payout of {money(amount)} to seller #{seller.id}")
            messages.success(request, f"Recorded a payout of {money(amount)}.")
        _refresh_attention()
        return redirect("manage_seller", pk=pk)
    products_qs = seller.products.order_by("-id")[:12]
    return _render(request, "seller_detail.html", {
        "section": "sellers", "seller": seller, "products": products_qs,
        "product_count": seller.products.count(),
    })


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

@staff_required
def customers(request):
    qs = User.objects.annotate(order_count=Count("orders", distinct=True)).order_by("-date_joined")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    page_obj = _page(request, qs)
    users = list(page_obj.object_list)
    spend = {
        r["order__user"]: r["v"]
        for r in OrderItem.objects.filter(order__user__in=users).exclude(order__status="cancelled")
        .exclude(order__payment_status__in=["pending", "failed"])
        .values("order__user").annotate(v=Sum(F("price") * F("quantity")))
    }
    sellers_ids = set(SellerAccount.objects.filter(user__in=users).values_list("user_id", flat=True))
    for u in users:
        u.spend = spend.get(u.pk, 0)
        u.is_seller = u.pk in sellers_ids
    return _render(request, "customers.html", {"section": "customers", "page_obj": page_obj, "users": users, "q": q})


# ---------------------------------------------------------------------------
# Coupons
# ---------------------------------------------------------------------------

class CouponForm(forms.ModelForm):
    class Meta:
        model = Coupon
        fields = ["code", "percent_off", "min_order_value", "expiry_date", "usage_limit", "per_user_limit", "active"]
        widgets = {"expiry_date": forms.DateInput(attrs={"type": "date"})}

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()

    def clean_percent_off(self):
        v = self.cleaned_data["percent_off"]
        if not 1 <= v <= 100:
            raise ValidationError("Use a value from 1 to 100.")
        return v


@staff_required
def coupons(request):
    form = CouponForm(request.POST or None, initial={"percent_off": 10, "per_user_limit": 1, "active": True})
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            if form.is_valid():
                c = form.save()
                _log(request, f"Created coupon {c.code}")
                messages.success(request, f"Coupon {c.code} created.")
                return redirect("manage_coupons")
            messages.error(request, "Please fix the coupon details below.")
        elif action in ("toggle", "delete"):
            c = get_object_or_404(Coupon, pk=request.POST.get("id"))
            if action == "toggle":
                c.active = not c.active
                c.save(update_fields=["active"])
                messages.success(request, f"{c.code} is now {'active' if c.active else 'paused'}.")
            else:
                _log(request, f"Deleted coupon {c.code}")
                c.delete()
                messages.success(request, "Coupon deleted.")
            return redirect("manage_coupons")
    rows = list(Coupon.objects.order_by("-id"))
    usage = dict(
        Order.objects.exclude(status="cancelled").exclude(coupon_code="")
        .values_list("coupon_code").annotate(n=Count("id"))
    )
    usage = {k.upper(): v for k, v in usage.items()}
    for c in rows:
        c.used = usage.get(c.code.upper(), 0)
    return _render(request, "coupons.html", {"section": "coupons", "coupons": rows, "form": form})


# ---------------------------------------------------------------------------
# Reviews & questions
# ---------------------------------------------------------------------------

@staff_required
def reviews(request):
    tab = "questions" if request.GET.get("tab") == "questions" else "reviews"
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "delete_review":
            r = get_object_or_404(Review, pk=request.POST.get("id"))
            _log(request, f"Deleted review #{r.id} on {r.product.name}")
            r.delete()
            messages.success(request, "Review deleted.")
        elif action == "answer":
            qn = get_object_or_404(Question, pk=request.POST.get("id"))
            qn.answer = request.POST.get("answer", "").strip()[:2000]
            qn.save(update_fields=["answer"])
            messages.success(request, "Answer published." if qn.answer else "Answer removed.")
        elif action == "delete_question":
            get_object_or_404(Question, pk=request.POST.get("id")).delete()
            messages.success(request, "Question deleted.")
        _refresh_attention()
        return redirect(f"{reverse('manage_reviews')}?tab={tab}")
    if tab == "questions":
        qs = sorted(
            Question.objects.select_related("product").order_by("-created_at")[:300],
            key=lambda x: bool(x.answer),  # unanswered first, newest first within each group
        )
        page_obj = Paginator(qs, 25).get_page(request.GET.get("page"))
    else:
        rating = request.GET.get("rating", "")
        qs = Review.objects.select_related("product").order_by("-created_at")
        if rating.isdigit():
            qs = qs.filter(rating=int(rating))
        page_obj = _page(request, qs)
    return _render(request, "reviews.html", {"section": "reviews", "tab": tab, "page_obj": page_obj, "rating": request.GET.get("rating", "")})


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------

@staff_required
def returns(request):
    if request.method == "POST":
        rr = get_object_or_404(ReturnRequest.objects.select_related("order_item__order"), pk=request.POST.get("id"))
        action = request.POST.get("action")
        note = request.POST.get("note", "").strip()[:255]
        if note:
            rr.admin_note = note
        if action == "approve":
            rr.status = "approved"
            rr.save()
            messages.success(request, "Return approved. The customer has been notified.")
        elif action == "reject":
            rr.status = "rejected"
            rr.save()
            messages.success(request, "Return rejected. The customer has been notified.")
        elif action == "refund":
            order = rr.order_item.order
            refunded_online = False
            if rr.refund_method == "original_payment" and order.payment_status == "paid" and order.stripe_payment_intent:
                if not payments.refund_amount(order, rr.order_item.subtotal, f"return-{rr.id}"):
                    messages.error(request, "Stripe couldn't process the refund. Try again, or refund from the Stripe dashboard and then mark it refunded.")
                    return redirect("manage_returns")
                refunded_online = True
            rr.status = "refunded"
            rr.save()
            if rr.order_item.product_id:
                Product.objects.filter(pk=rr.order_item.product_id).update(stock=F("stock") + rr.order_item.quantity)
            messages.success(request, f"Marked refunded{' and ' + money(rr.order_item.subtotal) + ' sent back to the card' if refunded_online else ''}. Stock returned.")
        _log(request, f"Return #{rr.id}: {action}")
        _refresh_attention()
        return redirect("manage_returns")
    status = request.GET.get("status", "requested")
    qs = ReturnRequest.objects.select_related("order_item", "order_item__order", "user").order_by("-created_at")
    if status in dict(ReturnRequest.STATUS_CHOICES):
        qs = qs.filter(status=status)
    return _render(request, "returns.html", {"section": "returns", "page_obj": _page(request, qs), "status": status, "status_choices": ReturnRequest.STATUS_CHOICES})


# ---------------------------------------------------------------------------
# Support messages
# ---------------------------------------------------------------------------

@staff_required
def support(request, pk=None):
    thread = get_object_or_404(ChatThread.objects.select_related("user"), pk=pk) if pk else None
    if request.method == "POST" and thread:
        action = request.POST.get("action")
        if action == "reply":
            text = request.POST.get("message", "").strip()[:2000]
            if text:
                ChatMessage.objects.create(thread=thread, sender="support", message=text, is_read=True)
                thread.is_resolved = False
                thread.save(update_fields=["is_resolved"])
                if thread.user_id:
                    Notification.objects.create(user=thread.user, message="Support replied to your message. Open the chat to read it.", link="/")
                messages.success(request, "Reply sent.")
        elif action in ("resolve", "reopen"):
            thread.is_resolved = action == "resolve"
            thread.save(update_fields=["is_resolved"])
            messages.success(request, "Conversation closed." if thread.is_resolved else "Conversation reopened.")
        _refresh_attention()
        return redirect("manage_support_thread", pk=thread.pk)

    show = request.GET.get("show", "open")
    threads = ChatThread.objects.select_related("user").annotate(
        msg_count=Count("messages"),
        unread=Count("messages", filter=Q(messages__sender="user", messages__is_read=False)),
    ).filter(msg_count__gt=0)
    threads = threads.filter(is_resolved=(show == "closed")).order_by("-unread", "-created_at")[:100]
    last = {}
    for m in ChatMessage.objects.filter(thread__in=[t.pk for t in threads]).order_by("thread_id", "-created_at"):
        last.setdefault(m.thread_id, m)
    for t in threads:
        t.last = last.get(t.pk)
    chat = []
    if thread:
        chat = list(thread.messages.order_by("created_at"))
        thread.messages.filter(sender="user", is_read=False).update(is_read=True)
        _refresh_attention()
    return _render(request, "support.html", {"section": "support", "threads": threads, "thread": thread, "chat": chat, "show": show})


# ---------------------------------------------------------------------------
# Store settings
# ---------------------------------------------------------------------------

class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        exclude = []
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
            "hero_subtitle": forms.Textarea(attrs={"rows": 3}),
        }

    GROUPS = [
        ("Brand", "Your store's name, logo and colours. Changes show on every page.",
         ["site_name", "tagline", "logo_file", "logo_url", "favicon_url", "primary_color", "accent_color"]),
        ("Homepage", "The large banner at the top of the homepage.", ["hero_title", "hero_subtitle", "hero_image_url"]),
        ("Announcement bar", "A message across the top of every page, e.g. a sale.", ["banner_active", "banner_text", "banner_link"]),
        ("Shipping, tax & payment", "Applied at checkout.", ["shipping_flat_fee", "free_shipping_threshold", "tax_percent", "allow_cash_on_delivery"]),
        ("Contact & social", "Shown in the footer, emails and help page.",
         ["support_email", "support_phone", "company_address", "facebook_url", "instagram_url", "twitter_url", "youtube_url"]),
        ("Language", "", ["show_language_menu"]),
    ]

    def groups(self):
        return [(title, hint, [self[name] for name in names]) for title, hint, names in self.GROUPS]


@staff_required
def store_settings(request):
    obj = SiteSettings.objects.get_or_create(pk=1)[0]
    form = SiteSettingsForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST":
        upload = request.FILES.get("logo_file")
        if upload:
            from .security import validate_image_upload
            try:
                validate_image_upload(upload)
            except ValidationError as exc:
                form.add_error("logo_file", exc)
        if form.is_valid():
            form.save()
            _log(request, "Updated store settings")
            messages.success(request, "Settings saved. Your store has been updated.")
            return redirect("manage_settings")
        messages.error(request, "Please fix the highlighted fields.")
    return _render(request, "settings.html", {"section": "settings", "form": form})
