"""Seed three demo Presence rows that exercise every status badge colour.

Run via: uv run python manage.py shell < .claude/skills/screenshot-app/seed_demo.py

The rows are pinned so the in-process runner thread (presence/runner.py) leaves
their states alone: each gets an always-open window (00:00-23:59) and a
far-future next_transition_at, so on its first tick the runner finds nothing to
flip. Without this, enabled rows snap to their time-of-day window state.
"""
from datetime import time, timedelta

from django.utils import timezone

from presence.models import Presence

now = timezone.now()
future = now + timedelta(days=1)

common = dict(
    min_on_duration=timedelta(hours=1),
    max_on_duration=timedelta(hours=2),
    min_off_duration=timedelta(hours=1),
    max_off_duration=timedelta(hours=2),
    earliest_on=time(0, 0),
    latest_off=time(23, 59),
    timezone="Europe/London",
    earliest_on_relative_to_sunset=False,
    latest_off_relative_to_sunrise=False,
    city="",
)

Presence.objects.all().delete()
Presence.objects.create(
    identifier="lounge-lamp", name="Lounge Lamp", enabled=True,
    current_state="on", state_since=now, next_transition_at=future, **common,
)
Presence.objects.create(
    identifier="hall-light", name="Hall Light", enabled=True,
    current_state="off", state_since=now, next_transition_at=future, **common,
)
Presence.objects.create(
    identifier="garage", name="Garage Light", enabled=False,
    current_state="on", state_since=now, next_transition_at=future, **common,
)

for p in Presence.objects.order_by("name"):
    print(p.name, "enabled=", p.enabled, "state=", p.current_state)
