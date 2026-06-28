import secrets

import django.db.models.deletion
from django.db import migrations, models

import presence.models


def seed_access_keys(apps, schema_editor):
    """Create an initial access key and link every existing presence to it.

    Mints a fresh random value for the key (the API endpoint is always
    protected now). Skipped entirely when there are no presences.

    Uses ``secrets`` directly rather than the model helper: historical models
    exposed via ``apps.get_model`` carry no methods.
    """
    Presence = apps.get_model("presence", "Presence")
    if not Presence.objects.exists():
        return
    AccessKey = apps.get_model("presence", "AccessKey")
    value = secrets.token_urlsafe(32)
    key = AccessKey.objects.create(name="Default", value=value)
    Presence.objects.filter(access_key__isnull=True).update(access_key=key)


class Migration(migrations.Migration):

    dependencies = [
        ("presence", "0008_alter_presence_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="AccessKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "name",
                    models.CharField(
                        help_text="Human-readable label for this key (e.g. 'Living room').",
                        max_length=64,
                        unique=True,
                    ),
                ),
                (
                    "value",
                    models.CharField(
                        default=presence.models.generate_access_key_value,
                        help_text=(
                            "The secret sent in the X-API-Key header. Auto-generated; treat "
                            "it as a password and do not share it in clear text."
                        ),
                        max_length=64,
                        unique=True,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text="When this key was created. Stored and shown in UTC.",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        help_text="When this key was last saved. Stored and shown in UTC.",
                    ),
                ),
            ],
            options={
                "verbose_name": "Access key",
                "verbose_name_plural": "Access keys",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="presence",
            name="access_key",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="presences",
                to="presence.accesskey",
            ),
        ),
        migrations.RunPython(seed_access_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="presence",
            name="access_key",
            field=models.ForeignKey(
                help_text=(
                    "Access key whose value the API requires in the X-API-Key header "
                    "to read this presence. A key cannot be deleted while in use."
                ),
                on_delete=django.db.models.deletion.PROTECT,
                related_name="presences",
                to="presence.accesskey",
            ),
        ),
    ]
