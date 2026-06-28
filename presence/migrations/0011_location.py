import django.db.models.deletion
from django.db import migrations, models


def seed_default_location(apps, schema_editor):
    """Create the ``Default`` location and link every existing presence to it.

    Unlike the access-key seed (0009), the Default location is created on
    *every* database, even one with no presences yet: it is the initial value
    of every new presence and is protected from deletion, so it must always
    exist. ``get_or_create`` keeps the migration idempotent.
    """
    Location = apps.get_model("presence", "Location")
    default, _ = Location.objects.get_or_create(name="Default")
    Presence = apps.get_model("presence", "Presence")
    Presence.objects.filter(location__isnull=True).update(location=default)


class Migration(migrations.Migration):

    dependencies = [
        ("presence", "0010_accesskey_last_generated_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="Location",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "name",
                    models.CharField(
                        help_text="Human-readable label for this location (e.g. 'Office').",
                        max_length=64,
                        unique=True,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text="When this location was created. Stored and shown in UTC.",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        help_text="When this location was last saved. Stored and shown in UTC.",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="presence",
            name="location",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="presences",
                to="presence.location",
            ),
        ),
        migrations.RunPython(seed_default_location, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="presence",
            name="location",
            field=models.ForeignKey(
                help_text=(
                    "Location this presence belongs to. A location cannot be "
                    "deleted while presences reference it."
                ),
                on_delete=django.db.models.deletion.PROTECT,
                related_name="presences",
                to="presence.location",
            ),
        ),
    ]
