"""
Stripe Checkout integration.

Flow
----
1. Checkout creates the Order (status "pending", payment_status "pending")
   and reserves stock inside a database transaction.
2. ``create_checkout_session`` sends the customer to Stripe's hosted
   payment page (cards, Apple Pay, Google Pay, Link ... whatever is enabled
   in your Stripe dashboard).
3. Stripe redirects back to /payment/success/ and, independently, calls
   our signed webhook at /payment/stripe/webhook/. The webhook is the
   source of truth: it checks the amount and marks the order paid.
4. If the customer abandons the page, Stripe sends
   ``checkout.session.expired`` (after 30 minutes) and we cancel the order
   and put the stock back.

Configuration (environment variables)
-------------------------------------
STRIPE_SECRET_KEY        sk_test_... / sk_live_...
STRIPE_PUBLISHABLE_KEY   pk_test_... / pk_live_...  (not required by
                         hosted Checkout, kept for future Elements use)
STRIPE_WEBHOOK_SECRET    whsec_...  (from Dashboard > Developers > Webhooks,
                         or from `stripe listen` when testing locally)
STORE_CURRENCY           usd (default), eur, gbp ...

Until STRIPE_SECRET_KEY is set, card payments are hidden at checkout.
"""
import json
import uuid
import logging
import time
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.urls import reverse

logger = logging.getLogger(__name__)

CHECKOUT_SESSION_LIFETIME_SECONDS = 30 * 60  # Stripe's minimum


class PaymentError(Exception):
    """Raised when Stripe can't be reached or rejects a request. The
    message is safe to show to customers."""


def is_configured():
    return bool(getattr(settings, "STRIPE_SECRET_KEY", ""))


def webhook_configured():
    return bool(getattr(settings, "STRIPE_WEBHOOK_SECRET", ""))


def _stripe():
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    stripe.max_network_retries = 2
    return stripe


def to_cents(amount):
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _absolute(request, path):
    return request.build_absolute_uri(path)


def create_checkout_session(request, order):
    """Creates a Stripe Checkout Session for ``order`` and returns its URL."""
    stripe = _stripe()
    currency = settings.STORE_CURRENCY.lower()

    line_items = []
    for item in order.items.all():
        line_items.append({
            "quantity": item.quantity,
            "price_data": {
                "currency": currency,
                "unit_amount": to_cents(item.price),
                "product_data": {"name": item.product_name[:250]},
            },
        })
    if order.shipping_amount:
        line_items.append({
            "quantity": 1,
            "price_data": {
                "currency": currency,
                "unit_amount": to_cents(order.shipping_amount),
                "product_data": {"name": "Shipping"},
            },
        })
    if order.tax_amount:
        line_items.append({
            "quantity": 1,
            "price_data": {
                "currency": currency,
                "unit_amount": to_cents(order.tax_amount),
                "product_data": {"name": "Tax"},
            },
        })

    params = {
        "mode": "payment",
        "line_items": line_items,
        "client_reference_id": str(order.id),
        "metadata": {"order_id": str(order.id)},
        "payment_intent_data": {"metadata": {"order_id": str(order.id)}},
        "success_url": _absolute(request, reverse("payment_success")) + "?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": _absolute(request, reverse("payment_cancel", args=[order.id])),
        "expires_at": int(time.time()) + CHECKOUT_SESSION_LIFETIME_SECONDS,
    }
    email = order.contact_email
    if email:
        params["customer_email"] = email

    try:
        if order.discount_amount:
            coupon = stripe.Coupon.create(
                amount_off=to_cents(order.discount_amount),
                currency=currency,
                duration="once",
                name=(f"Code {order.coupon_code}" if order.coupon_code else "Discount")[:40],
                max_redemptions=1,
            )
            params["discounts"] = [{"coupon": coupon.id}]
        session = stripe.checkout.Session.create(
            idempotency_key=f"order-{order.id}-{uuid.uuid4().hex}",
            **params,
        )
    except Exception as exc:  # stripe.StripeError and network failures
        logger.exception("Stripe Checkout Session creation failed for order %s", order.id)
        raise PaymentError("We couldn't start the secure payment page. Please try again in a moment.") from exc

    order.stripe_session_id = session.id
    order.save(update_fields=["stripe_session_id"])
    return session.url


def retrieve_session(session_id):
    stripe = _stripe()
    try:
        return stripe.checkout.Session.retrieve(session_id).to_dict()
    except Exception:
        logger.exception("Could not retrieve Stripe session %s", session_id)
        return None


def parse_webhook(payload: bytes, signature: str):
    """Verifies the Stripe signature and returns the event as a plain dict.
    Raises ValueError if the payload or signature is invalid."""
    stripe = _stripe()
    try:
        stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        raise ValueError("Invalid Stripe webhook") from exc
    return json.loads(payload)


def refund_order(order):
    """Refunds a paid card order in full. Returns True on success."""
    if not order.stripe_payment_intent:
        return False
    stripe = _stripe()
    try:
        stripe.Refund.create(
            payment_intent=order.stripe_payment_intent,
            idempotency_key=f"refund-order-{order.id}",
        )
    except Exception:
        logger.exception("Stripe refund failed for order %s", order.id)
        return False
    return True


def refund_amount(order, amount, key):
    """Refunds part of a paid card order (e.g. one returned item).
    ``key`` makes the refund idempotent. Returns True on success."""
    if not order.stripe_payment_intent or not amount:
        return False
    stripe = _stripe()
    try:
        stripe.Refund.create(
            payment_intent=order.stripe_payment_intent,
            amount=to_cents(amount),
            idempotency_key=f"refund-{key}",
        )
    except Exception:
        logger.exception("Stripe partial refund failed for order %s", order.id)
        return False
    return True


# ---------------------------------------------------------------------------
# Order state changes driven by Stripe. All idempotent: Stripe may deliver the
# same event more than once, and the success page may race the webhook.
# ---------------------------------------------------------------------------

def mark_order_paid(order_id, session):
    """Marks the order paid if ``session`` (a Checkout Session dict) is a
    completed, fully-paid session for exactly this order's amount.
    Returns (order, newly_paid)."""
    from .models import Order

    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if not order:
            return None, False
        if order.payment_status == "paid":
            return order, False
        if session.get("id") != order.stripe_session_id:
            logger.warning("Stripe session %s does not match order %s", session.get("id"), order.id)
            return order, False
        if session.get("payment_status") != "paid":
            return order, False
        if session.get("amount_total") != order.total_cents or (session.get("currency") or "").lower() != order.currency.lower():
            logger.error(
                "Amount mismatch for order %s: Stripe %s %s, expected %s %s",
                order.id, session.get("amount_total"), session.get("currency"), order.total_cents, order.currency,
            )
            return order, False
        order.payment_status = "paid"
        order.payment_method = "card"
        order.cod_fallback = False
        order.stripe_payment_intent = session.get("payment_intent") or ""
        if order.status == "pending":
            order.status = "confirmed"
        order.save(update_fields=["payment_status", "payment_method", "cod_fallback", "stripe_payment_intent", "status"])
    return order, True


def release_unpaid_order(order_id, reason="failed"):
    """Cancels an unpaid card order and returns its stock. A cash-on-delivery
    order whose advance online payment was abandoned goes back to cash on
    delivery instead."""
    from .models import Order

    with transaction.atomic():
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if not order or order.payment_status == "paid" or order.status == "cancelled":
            return order
        if order.cod_fallback:
            order.cod_fallback = False
            order.payment_method = "cod"
            order.payment_status = "not_applicable"
            order.stripe_session_id = ""
            order.save(update_fields=["cod_fallback", "payment_method", "payment_status", "stripe_session_id"])
            return order
        order.payment_status = reason
        order.status = "cancelled"
        order.save(update_fields=["payment_status", "status"])
        restock(order)
    return order


def restock(order):
    from django.db.models import F
    from .models import Product

    for item in order.items.all():
        if item.product_id:
            Product.objects.filter(pk=item.product_id).update(stock=F("stock") + item.quantity)
