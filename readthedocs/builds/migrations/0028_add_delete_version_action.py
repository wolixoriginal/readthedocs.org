# Generated by Django 2.2.16 on 2020-11-05 19:26

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("builds", "0027_add_privacy_level_automation_rules"),
    ]

    operations = [
        migrations.AlterField(
            model_name="versionautomationrule",
            name="action",
            field=models.CharField(
                choices=[
                    ("activate-version", "Activate version"),
                    ("hide-version", "Hide version"),
                    ("make-version-public", "Make version public"),
                    ("make-version-private", "Make version private"),
                    ("set-default-version", "Set version as default"),
                    ("delete-version", "Delete version (on branch/tag deletion)"),
                ],
                help_text="Action to apply to matching versions",
                max_length=32,
                verbose_name="Action",
            ),
        ),
    ]
