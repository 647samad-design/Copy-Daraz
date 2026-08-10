from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bees', '0007_backfill_referral_codes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='profile',
            name='referral_code',
            field=models.CharField(blank=True, max_length=12, unique=True),
        ),
    ]
