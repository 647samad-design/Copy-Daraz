import random
import string
from django.db import migrations


def backfill_referral_codes(apps, schema_editor):
    Profile = apps.get_model("daraz", "Profile")
    existing_codes = set()
    for profile in Profile.objects.all():
        if profile.referral_code:
            existing_codes.add(profile.referral_code)

    for profile in Profile.objects.filter(referral_code=""):
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        while code in existing_codes:
            code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        existing_codes.add(code)
        profile.referral_code = code
        profile.save(update_fields=["referral_code"])


class Migration(migrations.Migration):

    dependencies = [
        ('daraz', '0006_searchlog_order_guest_email_profile_email_verified_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_referral_codes, migrations.RunPython.noop),
    ]
