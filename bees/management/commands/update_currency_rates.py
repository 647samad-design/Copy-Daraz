from django.core.management.base import BaseCommand

from bees import currency


class Command(BaseCommand):
    help = "Download today's exchange rates for the shop's extra currencies. Run daily."

    def handle(self, *args, **options):
        from bees.models import Currency
        if not Currency.objects.filter(active=True).exists():
            self.stdout.write("  No extra currencies switched on - nothing to update")
            return
        updated, error = currency.update_rates()
        # A blocked network is not an error for the daily job: rates stay as they were.
        self.stdout.write(f"  {error}" if error else f"  {updated} currency rate(s) updated")
