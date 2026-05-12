from django.db import migrations, models
from django.utils.text import slugify

import presence.models


def backfill_identifiers(apps, schema_editor):
    """Populate `identifier` for any pre-existing rows.

    Uses Django's slugify on `name`, falling back to `presence-<pk>` if the
    name slugifies to an empty string. Disambiguates collisions by appending
    a numeric suffix. The result is then truncated to 63 characters to fit
    the RFC 1123 DNS label limit.
    """
    Presence = apps.get_model("presence", "Presence")
    used: set[str] = set()
    for row in Presence.objects.all().order_by("pk"):
        base = slugify(row.name) or f"presence-{row.pk}"
        candidate = base[:63]
        suffix_n = 2
        while candidate in used or Presence.objects.filter(identifier=candidate).exclude(pk=row.pk).exists():
            suffix = f"-{suffix_n}"
            candidate = (base[: 63 - len(suffix)] + suffix)
            suffix_n += 1
        used.add(candidate)
        row.identifier = candidate
        row.save(update_fields=["identifier"])


class Migration(migrations.Migration):

    dependencies = [
        ("presence", "0004_presence_city_presence_earliest_on_offset_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="presence",
            name="identifier",
            field=models.CharField(max_length=63, null=True),
        ),
        migrations.RunPython(backfill_identifiers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="presence",
            name="identifier",
            field=models.CharField(
                max_length=63,
                unique=True,
                validators=[presence.models.validate_dns_label],
                help_text=(
                    "URL-safe identifier used in the REST API path. Must be an "
                    "RFC 1123 DNS label: 1-63 lowercase letters/digits with "
                    "optional internal hyphens."
                ),
            ),
        ),
    ]
