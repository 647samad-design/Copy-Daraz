from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from bees.models import Notification, SellerAccount, SiteSettings


class Command(BaseCommand):
    help = "Remind sellers 3 days before their paid plan ends. Run daily."

    def handle(self, *args, **options):
        now = timezone.now()
        due = SellerAccount.objects.select_related("user", "plan").filter(
            plan__price__gt=0, plan_reminder_sent=False,
            plan_expires_at__gt=now, plan_expires_at__lte=now + timedelta(days=3),
        )
        site = SiteSettings.load()
        sent = 0
        for seller in due:
            when = f"{seller.plan_expires_at:%b %d}"
            text = f"Your {seller.plan.name} plan ends on {when}. Renew it to keep your lower commission and badge."
            Notification.objects.create(user=seller.user, message=text[:255], link="/seller/plan/")
            if seller.user.email:
                try:
                    send_mail(f"Your {seller.plan.name} plan ends on {when}",
                              f"Hi {seller.display_name},\n\n{text}\n\nRenew from Seller Center > Plan.\n\n{site.site_name}",
                              settings.DEFAULT_FROM_EMAIL, [seller.user.email], fail_silently=True)
                except Exception:
                    pass
            SellerAccount.objects.filter(pk=seller.pk).update(plan_reminder_sent=True)
            sent += 1
        self.stdout.write(f"  {sent} plan reminder(s) sent")
