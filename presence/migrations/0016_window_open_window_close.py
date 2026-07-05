# Issue #59: amalgamate each window-edge trio (time, relative-to-sun flag,
# signed offset) into a single string — "HH:MM" for a wall-clock time,
# "+HH:MM"/"-HH:MM" for a solar offset. The converters reuse the model
# module's canonical parse/format helpers so the stored form always matches
# what the application reads back.

from django.db import migrations, models

import presence.models
from presence.models import format_window_edge, parse_window_edge


def _amalgamate_window_edges(apps, schema_editor):
    """Fold the legacy edge trios into window_open / window_close.

    A row whose legacy fields are inconsistent (e.g. a solar flag with no
    offset, or no wall-clock time in absolute mode) raises here and aborts
    the migration rather than silently inventing an edge.
    """
    Presence = apps.get_model("presence", "Presence")
    for row in Presence.objects.all().iterator():
        if row.earliest_on_relative_to_sunset:
            row.window_open = format_window_edge(row.earliest_on_offset)
        else:
            row.window_open = format_window_edge(row.earliest_on)
        if row.latest_off_relative_to_sunrise:
            row.window_close = format_window_edge(row.latest_off_offset)
        else:
            row.window_close = format_window_edge(row.latest_off)
        row.save(update_fields=["window_open", "window_close"])


def _split_window_edges(apps, schema_editor):
    """Reverse: expand the strings back into the legacy edge trios."""
    from datetime import timedelta

    Presence = apps.get_model("presence", "Presence")
    for row in Presence.objects.all().iterator():
        open_edge = parse_window_edge(row.window_open)
        if isinstance(open_edge, timedelta):
            row.earliest_on_relative_to_sunset = True
            row.earliest_on = None
            row.earliest_on_offset = open_edge
        else:
            row.earliest_on_relative_to_sunset = False
            row.earliest_on = open_edge
            row.earliest_on_offset = None
        close_edge = parse_window_edge(row.window_close)
        if isinstance(close_edge, timedelta):
            row.latest_off_relative_to_sunrise = True
            row.latest_off = None
            row.latest_off_offset = close_edge
        else:
            row.latest_off_relative_to_sunrise = False
            row.latest_off = close_edge
            row.latest_off_offset = None
        row.save(
            update_fields=[
                "earliest_on",
                "earliest_on_relative_to_sunset",
                "earliest_on_offset",
                "latest_off",
                "latest_off_relative_to_sunrise",
                "latest_off_offset",
            ]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("presence", "0015_alter_location_position"),
    ]

    operations = [
        migrations.AddField(
            model_name="presence",
            name="window_open",
            field=models.CharField(
                default="",
                help_text=(
                    "When the daily active window opens: HH:MM for a "
                    "wall-clock time in the location's timezone, or a "
                    "signed +HH:MM / -HH:MM offset from sunset (e.g. "
                    "-01:00 for one hour before sunset; the location must "
                    "then name a city)."
                ),
                max_length=6,
                validators=[presence.models.validate_window_edge],
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="presence",
            name="window_close",
            field=models.CharField(
                default="",
                help_text=(
                    "When the daily active window closes: HH:MM for a "
                    "wall-clock time in the location's timezone, or a "
                    "signed +HH:MM / -HH:MM offset from sunrise (e.g. "
                    "+00:30 for half an hour after sunrise). A close at "
                    "or before the open wraps past midnight."
                ),
                max_length=6,
                validators=[presence.models.validate_window_edge],
            ),
            preserve_default=False,
        ),
        migrations.RunPython(_amalgamate_window_edges, _split_window_edges),
        migrations.RemoveField(model_name="presence", name="earliest_on"),
        migrations.RemoveField(
            model_name="presence", name="earliest_on_relative_to_sunset"
        ),
        migrations.RemoveField(model_name="presence", name="earliest_on_offset"),
        migrations.RemoveField(model_name="presence", name="latest_off"),
        migrations.RemoveField(
            model_name="presence", name="latest_off_relative_to_sunrise"
        ),
        migrations.RemoveField(model_name="presence", name="latest_off_offset"),
    ]
