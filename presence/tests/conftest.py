"""Shared test helpers for the Presence model suite.

`make_presence` builds a *valid, unsaved* Presence in absolute (wall-clock)
window mode, linked to a persisted :class:`~presence.models.AccessKey` (every
presence requires one). Each test perturbs a single axis via keyword overrides,
so the baseline must always pass `clean()` / `full_clean(validate_unique=False)`.
Because a valid presence now needs a saved access key, the factory depends on
the ``db`` fixture. No factory_boy / freezegun: the model takes explicit
`now`/`on_date`, so plain instances and hand-built datetimes are sufficient.
"""
from datetime import time, timedelta

import pytest

from presence.models import AccessKey, Location, Presence

#: A complete, valid set of constructor kwargs (absolute window mode).
#: ``timezone`` and ``city`` now live on the Location (issue #43); the
#: ``make_presence`` factory routes those overrides onto the linked location.
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
    earliest_on_relative_to_sunset=False,
    latest_off_relative_to_sunrise=False,
)


@pytest.fixture
def access_key(db) -> AccessKey:
    """A saved access key for linking presences in tests."""
    return AccessKey.objects.create(name="Test Key")


@pytest.fixture
def location(db) -> Location:
    """A saved location for linking presences in tests.

    Distinct from the migration-seeded ``Default`` location so tests can tell
    the two apart. Carries the timezone/city the presence windows use.
    """
    return Location.objects.create(name="Test Location", timezone="UTC")


@pytest.fixture
def make_presence(access_key, location):
    """Return a factory: ``make_presence(**overrides) -> Presence`` (unsaved).

    The instance is linked to the shared :func:`access_key` and
    :func:`location` unless an ``access_key`` / ``location`` override is
    supplied. ``timezone`` and ``city`` now live on the Location (issue #43),
    so those overrides are applied to the linked location (which is saved) and
    not passed to the presence.
    """

    def _factory(**overrides) -> Presence:
        used_location = overrides.pop("location", location)
        if "timezone" in overrides:
            used_location.timezone = overrides.pop("timezone")
        if "city" in overrides:
            used_location.city = overrides.pop("city")
        used_location.save()
        kwargs = {
            "access_key": access_key,
            "location": used_location,
            **VALID_KWARGS,
            **overrides,
        }
        return Presence(**kwargs)

    return _factory


@pytest.fixture(autouse=True)
def _clear_rate_limit_cache():
    """Reset the local-memory cache between tests.

    The rate limiter (``presence.ratelimit``) records failures in Django's
    default LocMemCache, which persists for the whole test process. Clear it
    around every test so throttle counters never leak across cases.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


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
