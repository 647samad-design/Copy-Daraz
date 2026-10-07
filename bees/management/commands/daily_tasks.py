"""Everything the store needs once a day, in one command - so a single
PythonAnywhere scheduled task covers it:

    cd ~/Lumen-Market && venv/bin/python manage.py daily_tasks

  1. cancel card orders that were never paid (safety net for webhooks)
  2. email cart reminders
  3. back up the database and images
  4. delete expired sign-in sessions
Each step runs even if an earlier one fails.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the store's daily maintenance jobs."

    def handle(self, *args, **options):
        failed = []
        for name in ("release_unpaid_orders", "send_cart_reminders", "backup_data", "clearsessions"):
            self.stdout.write(f"- {name}")
            try:
                call_command(name, stdout=self.stdout)
            except Exception as exc:  # keep going, report at the end
                failed.append(name)
                self.stderr.write(f"  {name} failed: {exc}")
        if failed:
            self.stderr.write(self.style.ERROR(f"Finished with errors in: {', '.join(failed)}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("Daily tasks finished."))
