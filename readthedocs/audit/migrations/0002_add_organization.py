# Generated by Django 2.2.24 on 2021-09-14 20:53

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0006_add_assets_cleaned"),
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="log_organization_id",
            field=models.IntegerField(
                blank=True, db_index=True, null=True, verbose_name="Organization ID"
            ),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="log_organization_slug",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=255,
                null=True,
                verbose_name="Organization slug",
            ),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="organization",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="organizations.Organization",
                verbose_name="Organization",
            ),
        ),
    ]
