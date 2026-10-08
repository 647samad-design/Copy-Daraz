from django.core.management.base import BaseCommand

from bees import badges


class Command(BaseCommand):
    help = "Recalculate the Top seller badge for every seller. Run daily."

    def handle(self, *args, **options):
        gained, lost = badges.update_all()
        self.stdout.write(f"  Top seller badges: {gained} gained, {lost} removed")
