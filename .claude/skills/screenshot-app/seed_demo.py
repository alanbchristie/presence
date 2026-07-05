"""Seed three demo Presence rows that exercise every status badge colour.

Run via: uv run python manage.py shell < .claude/skills/screenshot-app/seed_demo.py

The rows are pinned so the in-process runner thread (presence/runner.py) leaves
their states alone: each gets an always-open window (00:00-23:59) and a
far-future next_transition_at, so on its first tick the runner finds nothing to
flip. Without this, enabled rows snap to their time-of-day window state.

Each presence needs a Location (issue #43 moved timezone/city there); the demo
locations carry real astral cities so the Map page has markers to show.
"""
from datetime import timedelta

from django.utils import timezone

from presence.models import AccessKey, Location, Presence

now = timezone.now()
future = now + timedelta(days=1)

# Every presence needs an access key (issue #26); a single shared demo key is
# enough to satisfy the FK for the screenshots.
demo_key, _ = AccessKey.objects.get_or_create(name="Demo key")

home, _ = Location.objects.get_or_create(
    name="Home", defaults=dict(timezone="Europe/London", city="London")
)
office, _ = Location.objects.get_or_create(
    name="New York Office",
    defaults=dict(timezone="America/New_York", city="New York"),
)
lab, _ = Location.objects.get_or_create(
    name="Tokyo Lab", defaults=dict(timezone="Asia/Tokyo", city="Tokyo")
)

common = dict(
    access_key=demo_key,
    min_on_duration=timedelta(hours=1),
    max_on_duration=timedelta(hours=2),
    min_off_duration=timedelta(hours=1),
    max_off_duration=timedelta(hours=2),
    window_open="00:00",
    window_close="23:59",
)

Presence.objects.all().delete()
Presence.objects.create(
    identifier="lounge-lamp", name="Lounge Lamp", enabled=True, location=home,
    current_state="on", state_since=now, next_transition_at=future, **common,
)
Presence.objects.create(
    identifier="hall-light", name="Hall Light", enabled=True, location=office,
    current_state="off", state_since=now, next_transition_at=future, **common,
)
Presence.objects.create(
    identifier="garage", name="Garage Light", enabled=False, location=lab,
    current_state="on", state_since=now, next_transition_at=future, **common,
)

for p in Presence.objects.order_by("name"):
    print(p.name, "enabled=", p.enabled, "state=", p.current_state)
