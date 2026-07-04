"""Tests for the world map ("Map") page.

The page plots every :class:`~presence.models.Location` whose ``city`` is
known to astral's built-in database, using the city's latitude/longitude.
Locations without a city cannot be placed on the map and are listed
separately. The night-shadow rendering itself is client-side JavaScript,
so these tests cover the view contract: authentication, the plotted
location payload, and the unplottable list.
"""
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone

from presence.models import Location

pytestmark = pytest.mark.django_db


# --- model helper ----------------------------------------------------------


def test_coordinates_come_from_astral_city():
    location = Location(name="Office", timezone="Europe/London", city="London")

    coordinates = location.coordinates

    assert coordinates is not None
    latitude, longitude = coordinates
    assert latitude == pytest.approx(51.47, abs=0.1)
    assert longitude == pytest.approx(0.0, abs=0.1)


def test_coordinates_none_without_city():
    assert Location(name="Nowhere", timezone="UTC").coordinates is None


def test_coordinates_none_for_unknown_city():
    # ``city`` is validated on save, but the property must still fail soft
    # if a stale/unvalidated value slips through.
    location = Location(name="Odd", timezone="UTC", city="Not A Real City")
    assert location.coordinates is None


# --- view ------------------------------------------------------------------


def _login(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)
    return user


def test_map_redirects_anonymous_to_login(client):
    response = client.get(reverse("map"))
    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_map_rejects_post(client, django_user_model):
    _login(client, django_user_model)
    assert client.post(reverse("map")).status_code == 405


def test_map_renders_for_logged_in_user(client, django_user_model):
    _login(client, django_user_model)

    response = client.get(reverse("map"))

    assert response.status_code == 200
    assert "presence/map.html" in [t.name for t in response.templates]


def test_map_plots_locations_with_a_city(client, django_user_model):
    _login(client, django_user_model)
    office = Location.objects.create(
        name="Office", timezone="Europe/London", city="London"
    )

    response = client.get(reverse("map"))

    plotted = response.context["plotted"]
    assert len(plotted) == 1
    entry = plotted[0]
    assert entry["name"] == "Office"
    assert entry["city"] == "London"
    assert entry["timezone"] == "Europe/London"
    assert entry["latitude"] == pytest.approx(51.47, abs=0.1)
    assert entry["longitude"] == pytest.approx(0.0, abs=0.1)
    assert entry["url"] == reverse("location_detail", args=[office.pk])


def test_map_lists_cityless_locations_as_unplottable(client, django_user_model):
    _login(client, django_user_model)
    Location.objects.create(name="Shed", timezone="UTC")

    response = client.get(reverse("map"))

    plotted_names = [entry["name"] for entry in response.context["plotted"]]
    unplottable_names = [
        location.name for location in response.context["unplottable"]
    ]
    assert "Shed" not in plotted_names
    assert "Shed" in unplottable_names
    # The migration-seeded Default location has no city either.
    assert "Default" in unplottable_names


def test_map_marker_status_on_when_any_enabled_presence_is_on(
    client, django_user_model, make_presence
):
    _login(client, django_user_model)
    office = Location.objects.create(
        name="Office", timezone="Europe/London", city="London"
    )
    make_presence(
        identifier="a", name="A", location=office, enabled=True, current_state="on"
    ).save()
    make_presence(
        identifier="b", name="B", location=office, enabled=True, current_state="off"
    ).save()
    make_presence(
        identifier="c", name="C", location=office, enabled=False, current_state="off"
    ).save()

    entry = client.get(reverse("map")).context["plotted"][0]

    assert entry["status"] == "on"
    assert entry["on_count"] == 1
    assert entry["off_count"] == 1
    assert entry["disabled_count"] == 1


def test_map_marker_status_off_when_enabled_presences_are_all_off(
    client, django_user_model, make_presence
):
    _login(client, django_user_model)
    office = Location.objects.create(
        name="Office", timezone="Europe/London", city="London"
    )
    make_presence(
        identifier="a", name="A", location=office, enabled=True, current_state="off"
    ).save()
    make_presence(
        identifier="b", name="B", location=office, enabled=False, current_state="on"
    ).save()

    entry = client.get(reverse("map")).context["plotted"][0]

    # A disabled presence never counts as on, whatever its stored state.
    assert entry["status"] == "off"
    assert entry["on_count"] == 0
    assert entry["off_count"] == 1
    assert entry["disabled_count"] == 1


def test_map_marker_status_disabled_when_all_presences_disabled(
    client, django_user_model, make_presence
):
    _login(client, django_user_model)
    office = Location.objects.create(
        name="Office", timezone="Europe/London", city="London"
    )
    make_presence(
        identifier="a", name="A", location=office, enabled=False, current_state="off"
    ).save()

    entry = client.get(reverse("map")).context["plotted"][0]

    assert entry["status"] == "disabled"


def test_map_marker_status_disabled_without_presences(client, django_user_model):
    _login(client, django_user_model)
    Location.objects.create(name="Office", timezone="Europe/London", city="London")

    entry = client.get(reverse("map")).context["plotted"][0]

    assert entry["status"] == "disabled"
    assert entry["on_count"] == 0
    assert entry["off_count"] == 0
    assert entry["disabled_count"] == 0


def test_map_orders_plotted_locations_by_name(client, django_user_model):
    _login(client, django_user_model)
    Location.objects.create(name="Zurich HQ", timezone="Europe/Zurich", city="Zurich")
    Location.objects.create(name="Annex", timezone="Europe/London", city="London")

    response = client.get(reverse("map"))

    assert [e["name"] for e in response.context["plotted"]] == [
        "Annex",
        "Zurich HQ",
    ]


def test_map_plotted_entries_carry_the_location_id(client, django_user_model):
    _login(client, django_user_model)
    office = Location.objects.create(
        name="Office", timezone="Europe/London", city="London"
    )

    entry = client.get(reverse("map")).context["plotted"][0]

    assert entry["id"] == office.pk


# --- status endpoint (live marker refresh) ---------------------------------


def test_map_status_redirects_anonymous_to_login(client):
    response = client.get(reverse("map_status"))
    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_map_status_rejects_post(client, django_user_model):
    _login(client, django_user_model)
    assert client.post(reverse("map_status")).status_code == 405


def test_map_status_reports_every_location(client, django_user_model, make_presence):
    _login(client, django_user_model)
    office = Location.objects.create(
        name="Office", timezone="Europe/London", city="London"
    )
    shed = Location.objects.create(name="Shed", timezone="UTC")
    make_presence(
        identifier="a", name="A", location=office, enabled=True, current_state="on"
    ).save()

    payload = client.get(reverse("map_status")).json()

    by_id = {entry["id"]: entry for entry in payload["locations"]}
    assert by_id[office.pk]["status"] == "on"
    assert by_id[office.pk]["on_count"] == 1
    assert by_id[office.pk]["off_count"] == 0
    assert by_id[office.pk]["disabled_count"] == 0
    # Locations without a city are still reported; the client simply has
    # no marker to recolour for them.
    assert by_id[shed.pk]["status"] == "disabled"


def test_map_status_matches_page_aggregation(client, django_user_model, make_presence):
    """The endpoint and the page must never disagree on a dot's colour."""
    _login(client, django_user_model)
    office = Location.objects.create(
        name="Office", timezone="Europe/London", city="London"
    )
    make_presence(
        identifier="a", name="A", location=office, enabled=True, current_state="off"
    ).save()
    make_presence(
        identifier="b", name="B", location=office, enabled=False, current_state="on"
    ).save()

    page_entry = client.get(reverse("map")).context["plotted"][0]
    api_entry = {
        entry["id"]: entry
        for entry in client.get(reverse("map_status")).json()["locations"]
    }[office.pk]

    for key in ("status", "on_count", "off_count", "disabled_count", "presences"):
        assert api_entry[key] == page_entry[key]


# --- per-presence window detail (issue #52) ---------------------------------
#
# The marker tooltip answers "why is that light off right now?", so each
# location entry carries a per-presence detail list: live state, whether the
# presence is currently inside its active window, and the next transition
# time rendered in the location's timezone (day-prefixed when not today).


def _window_containing_now() -> dict:
    """Absolute UTC window edges guaranteed to contain the current time."""
    now = timezone.now()
    return {
        "earliest_on": (now - timedelta(hours=2)).time(),
        "latest_off": (now + timedelta(hours=2)).time(),
    }


def _window_excluding_now() -> dict:
    """Absolute UTC window edges guaranteed not to contain the current time."""
    now = timezone.now()
    return {
        "earliest_on": (now + timedelta(hours=1)).time(),
        "latest_off": (now + timedelta(hours=2)).time(),
    }


def test_map_marker_details_presence_on_inside_window(
    client, django_user_model, make_presence
):
    _login(client, django_user_model)
    office = Location.objects.create(name="Office", timezone="UTC", city="London")
    now = timezone.now()
    make_presence(
        identifier="lamp",
        name="Lamp",
        location=office,
        enabled=True,
        current_state="on",
        next_transition_at=now,
        **_window_containing_now(),
    ).save()

    entry = client.get(reverse("map")).context["plotted"][0]

    (detail,) = entry["presences"]
    assert detail["name"] == "Lamp"
    assert detail["state"] == "on"
    assert detail["in_window"] is True
    assert detail["next_transition"] == now.astimezone(
        ZoneInfo("UTC")
    ).strftime("%H:%M")


def test_map_marker_details_presence_off_outside_window(
    client, django_user_model, make_presence
):
    _login(client, django_user_model)
    office = Location.objects.create(name="Office", timezone="UTC", city="London")
    make_presence(
        identifier="lamp",
        name="Lamp",
        location=office,
        enabled=True,
        current_state="off",
        next_transition_at=None,
        **_window_excluding_now(),
    ).save()

    (detail,) = client.get(reverse("map")).context["plotted"][0]["presences"]

    assert detail["state"] == "off"
    assert detail["in_window"] is False
    assert detail["next_transition"] is None


def test_map_marker_details_disabled_presence_has_no_window_detail(
    client, django_user_model, make_presence
):
    # The runner skips disabled rows, so their stored next transition is
    # stale; the detail reports plain "disabled" with no window claims.
    _login(client, django_user_model)
    office = Location.objects.create(name="Office", timezone="UTC", city="London")
    make_presence(
        identifier="lamp",
        name="Lamp",
        location=office,
        enabled=False,
        current_state="on",
        next_transition_at=timezone.now(),
    ).save()

    (detail,) = client.get(reverse("map")).context["plotted"][0]["presences"]

    assert detail["state"] == "disabled"
    assert detail["in_window"] is None
    assert detail["next_transition"] is None


def test_map_marker_next_transition_renders_in_location_timezone(
    client, django_user_model, make_presence
):
    _login(client, django_user_model)
    office = Location.objects.create(
        name="Office", timezone="Pacific/Auckland", city="Wellington"
    )
    now = timezone.now()
    make_presence(
        identifier="lamp",
        name="Lamp",
        location=office,
        enabled=True,
        current_state="on",
        next_transition_at=now,
        **_window_containing_now(),
    ).save()

    (detail,) = client.get(reverse("map")).context["plotted"][0]["presences"]

    # "now" rendered in the location's zone is always "today" there, so no
    # day prefix; and Pacific/Auckland sits a whole number of hours from
    # UTC, so the local rendering can never coincide with the UTC one.
    local = now.astimezone(ZoneInfo("Pacific/Auckland"))
    assert detail["next_transition"] == local.strftime("%H:%M")
    assert detail["next_transition"] != now.astimezone(ZoneInfo("UTC")).strftime(
        "%H:%M"
    )


def test_map_marker_next_transition_shows_day_when_not_today(
    client, django_user_model, make_presence
):
    _login(client, django_user_model)
    office = Location.objects.create(name="Office", timezone="UTC", city="London")
    transition = timezone.now() + timedelta(days=3)
    make_presence(
        identifier="lamp",
        name="Lamp",
        location=office,
        enabled=True,
        current_state="off",
        next_transition_at=transition,
        **_window_containing_now(),
    ).save()

    (detail,) = client.get(reverse("map")).context["plotted"][0]["presences"]

    local = transition.astimezone(ZoneInfo("UTC"))
    assert detail["next_transition"] == local.strftime("%a %H:%M")


def test_map_marker_details_ordered_by_presence_name(
    client, django_user_model, make_presence
):
    _login(client, django_user_model)
    office = Location.objects.create(name="Office", timezone="UTC", city="London")
    make_presence(
        identifier="z", name="Zebra lamp", location=office, enabled=True
    ).save()
    make_presence(
        identifier="a", name="attic light", location=office, enabled=True
    ).save()

    details = client.get(reverse("map")).context["plotted"][0]["presences"]

    assert [detail["name"] for detail in details] == ["attic light", "Zebra lamp"]


def test_map_status_carries_presence_details(
    client, django_user_model, make_presence
):
    _login(client, django_user_model)
    office = Location.objects.create(name="Office", timezone="UTC", city="London")
    make_presence(
        identifier="lamp",
        name="Lamp",
        location=office,
        enabled=True,
        current_state="on",
        **_window_containing_now(),
    ).save()

    payload = client.get(reverse("map_status")).json()

    entry = {e["id"]: e for e in payload["locations"]}[office.pk]
    (detail,) = entry["presences"]
    assert detail["name"] == "Lamp"
    assert detail["state"] == "on"
    assert detail["in_window"] is True
