# Generated by Django 1.11.16 on 2018-12-10 21:28
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0003_add_missing_model_change_migrations"),
    ]

    operations = [
        migrations.AddField(
            model_name="integration",
            name="secret",
            field=models.CharField(
                blank=True,
                default=None,
                help_text="Secret used to validate the payload of the webhook",
                max_length=255,
                null=True,
            ),
        ),
    ]
