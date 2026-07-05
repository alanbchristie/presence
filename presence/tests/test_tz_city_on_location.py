"""Tests for moving ``timezone`` and ``city`` onto Location (issue #43).

The window times are still per-presence, but the timezone they are interpreted
in (and the city used for solar edges) now live on the presence's Location. The
API continues to expose both. The deprecated Presence.timezone / Presence.city
columns were dropped entirely in issue #52.
"""
import json
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.urls import reverse

from presence.forms import LocationForm, PresenceForm
from presence.models import Location, Presence

from .conftest import VALID_KWARGS

pytestmark = pytest.mark.django_db


# --- Location now carries timezone / city --------------------------------


def test_location_timezone_defaults_to_utc():
    assert Location(name="Office").timezone == "UTC"


def test_location_rejects_invalid_timezone():
    with pytest.raises(ValidationError) as exc:
        Location(name="Office", timezone="Mars/Phobos").full_clean()
    assert "timezone" in exc.value.message_dict


def test_location_rejects_unknown_city():
    with pytest.raises(ValidationError) as exc:
        Location(name="Office", timezone="UTC", city="Nowhereville").full_clean()
    assert "city" in exc.value.message_dict


# --- window computation reads the location's timezone --------------------


def test_window_open_uses_location_timezone(make_presence):
    # 20:00 wall-clock interpreted in the location's timezone, not UTC.
    p = make_presence(timezone="Europe/London", window_open="20:00")
    open_dt, _ = p._window_for_date(__import__("datetime").date(2026, 7, 15))
    # BST is UTC+1, so 20:00 London is 19:00 UTC.
    assert open_dt.astimezone(ZoneInfo("UTC")).hour == 19


def test_solar_edge_requires_city_on_location(make_presence):
    # Solar edge but the location has no city -> non-field validation error.
    p = make_presence(window_open="-00:30", city="")
    with pytest.raises(ValidationError) as exc:
        p.clean()
    assert NON_FIELD_ERRORS in exc.value.message_dict

    # Give the location a city and it validates.
    make_presence(window_open="-00:30", city="London").clean()


# --- API still exposes both values, sourced from the location ------------


def test_api_exposes_location_timezone_and_city(client, make_presence, access_key):
    p = make_presence(timezone="Europe/London", city="London")
    p.save()
    url = reverse("presence:detail", args=[VALID_KWARGS["identifier"]])

    response = client.get(url, HTTP_X_API_KEY=access_key.value)

    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["timezone"] == "Europe/London"
    assert payload["city"] == "London"


# --- forms: location gains the fields, presence loses them ---------------


def test_location_form_includes_timezone_and_city():
    fields = LocationForm().fields
    assert "timezone" in fields
    assert "city" in fields


def test_presence_form_drops_deprecated_timezone_and_city():
    fields = PresenceForm().fields
    assert "timezone" not in fields
    assert "city" not in fields


# --- the deprecated Presence columns are gone (issue #52) -----------------
#
# (The 0012 data-migration tests that lived here exercised its copy function
# against the live registry; they could only work while the deprecated
# Presence columns still existed, so they were retired with the columns.)


def test_presence_model_drops_deprecated_timezone_and_city():
    field_names = {field.name for field in Presence._meta.get_fields()}
    assert "timezone" not in field_names
    assert "city" not in field_names
