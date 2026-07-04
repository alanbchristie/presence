"""Tests for the world map ("Map") page.

The page plots every :class:`~presence.models.Location` whose ``city`` is
known to astral's built-in database, using the city's latitude/longitude.
Locations without a city cannot be placed on the map and are listed
separately. The night-shadow rendering itself is client-side JavaScript,
so these tests cover the view contract: authentication, the plotted
location payload, and the unplottable list.
"""
import pytest
from django.urls import reverse

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

    for key in ("status", "on_count", "off_count", "disabled_count"):
        assert api_entry[key] == page_entry[key]
