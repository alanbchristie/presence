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


def test_map_orders_plotted_locations_by_name(client, django_user_model):
    _login(client, django_user_model)
    Location.objects.create(name="Zurich HQ", timezone="Europe/Zurich", city="Zurich")
    Location.objects.create(name="Annex", timezone="Europe/London", city="London")

    response = client.get(reverse("map"))

    assert [e["name"] for e in response.context["plotted"]] == [
        "Annex",
        "Zurich HQ",
    ]
