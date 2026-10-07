"""Back-in-stock emails."""
import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def notify_restocked(product_id, variant_id=None):
    """After stock goes up: email everyone waiting for this product (or
    this size/colour of it) that is now available. Runs after the database
    commit; each person is emailed once."""
    def _send():
        from .models import Product, StockAlert
        from .order_emails import absolute
        from .views import _send_html_email
        from django.urls import reverse
        pending = StockAlert.objects.filter(product_id=product_id, notified_at__isnull=True).select_related("variant")
        if not pending.exists():
            return
        product = Product.objects.live().filter(pk=product_id).first()
        if not product:
            return
        url = absolute(reverse("product_detail", args=[product.pk]))
        for alert in pending:
            if alert.variant_id:
                if alert.variant is None or alert.variant.product_id != product.pk:
                    continue
                if alert.variant.stock <= 0:
                    continue
                name = f"{product.name} ({alert.variant.label})"
            else:
                if product.stock <= 0:
                    continue
                name = product.name
            claimed = StockAlert.objects.filter(pk=alert.pk, notified_at__isnull=True).update(notified_at=timezone.now())
            if claimed:
                _send_html_email(f"{name} is back in stock", "bees/emails/back_in_stock.html",
                                 {"product": product, "name": name, "url": url}, alert.email)
    transaction.on_commit(_send)
