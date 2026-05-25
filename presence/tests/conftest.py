"""Shared test helpers for the Presence model suite.

`make_presence` builds a *valid, unsaved* Presence in absolute (wall-clock)
window mode. Each test perturbs a single axis via keyword overrides, so the
baseline must always pass `clean()` / `full_clean(validate_unique=False)`.
No factory_boy / freezegun: the model takes explicit `now`/`on_date`, so plain
instances and hand-built datetimes are sufficient.
"""
from datetime import time, timedelta

import pytest

from presence.models import Presence

#: A complete, valid set of constructor kwargs (absolute window mode).
VALID_KWARGS = dict(
    identifier="lamp",
    name="Lamp",
    enabled=True,
    min_on_duration=timedelta(hours=1),
    max_on_duration=timedelta(hours=1),
    min_off_duration=timedelta(hours=1),
    max_off_duration=timedelta(hours=1),
    earliest_on=time(20, 0),
    latest_off=time(23, 0),
    timezone="UTC",
    earliest_on_relative_to_sunset=False,
    latest_off_relative_to_sunrise=False,
    city="",
)


@pytest.fixture
def make_presence():
    """Return a factory: ``make_presence(**overrides) -> Presence`` (unsaved)."""

    def _factory(**overrides) -> Presence:
        return Presence(**{**VALID_KWARGS, **overrides})

    return _factory


@pytest.fixture(autouse=True)
def _plain_static_storage(settings):
    """Render `{% static %}` without a collectstatic manifest.

    Tests run with DEBUG off, which selects WhiteNoise's manifest storage; that
    raises ``Missing staticfiles manifest entry`` for the vendored Bootstrap
    assets because the suite never runs collectstatic. Swap in the plain
    storage (which just prefixes STATIC_URL) so templates render.
    """
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
