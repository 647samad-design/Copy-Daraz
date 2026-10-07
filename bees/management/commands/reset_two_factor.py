"""Turns off two-step sign-in for an account that lost its phone and
backup codes. They'll be asked to set it up again.

    python manage.py reset_two_factor <username or email>
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from bees.models import AuditLog, Profile


class Command(BaseCommand):
    help = "Turn off two-step sign-in for one account."

    def add_arguments(self, parser):
        parser.add_argument("who")

    def handle(self, *args, **options):
        users = get_user_model().objects.filter(Q(username__iexact=options["who"]) | Q(email__iexact=options["who"]))
        if users.count() != 1:
            raise CommandError("No single account matches that username/email.")
        user = users.get()
        Profile.objects.filter(user=user).update(totp_enabled=False, totp_secret="", backup_codes=[], totp_last_step=0)
        AuditLog.objects.create(user=user, action="Two-step sign-in reset from the server console")
        self.stdout.write(self.style.SUCCESS(f"Two-step sign-in turned off for {user.username}. They can sign in with just their password and set it up again."))
