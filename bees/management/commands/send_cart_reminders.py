"""Emails signed-in customers who left items in their cart.

One reminder per cart, sent 24 hours after the cart last changed (carts
older than 7 days are skipped). Customers can switch these off under
Account > Privacy & data. Run daily (see daily_tasks).
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from bees.cart import lines, totals
from bees.models import Order, Profile
from bees.order_emails import absolute


class Command(BaseCommand):
    help = "Email reminders about carts left for more than 24 hours."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24)

    def handle(self, *args, **options):
        from bees.views import _send_html_email
        now = timezone.now()
        due = Profile.objects.filter(
            cart_reminders=True, cart_reminder_sent=False,
            cart_updated_at__lte=now - timedelta(hours=options["hours"]),
            cart_updated_at__gte=now - timedelta(days=7),
            user__is_active=True,
        ).exclude(user__email="").select_related("user")
        sent = 0
        for profile in due:
            # Claim first so two runs never email twice.
            if not Profile.objects.filter(pk=profile.pk, cart_reminder_sent=False).update(cart_reminder_sent=True):
                continue
            if Order.objects.filter(user=profile.user, created_at__gte=profile.cart_updated_at).exists():
                continue
            rows = lines(profile.saved_cart)
            if not rows:
                continue
            total, count = totals(rows)
            _send_html_email(
                "You left something in your cart",
                "bees/emails/cart_reminder.html",
                {"user": profile.user, "rows": rows[:6], "more": max(len(rows) - 6, 0), "total": total,
                 "cart_url": absolute(reverse("cart")), "prefs_url": absolute(reverse("account_privacy"))},
                profile.user.email,
            )
            sent += 1
        self.stdout.write(self.style.SUCCESS(f"Cart reminders sent: {sent}"))
