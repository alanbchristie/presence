"""Tests for the optional Location ``position`` (issue #54).

``position`` stores a decimal "lat,lon" pair, entered as decimals
("51.520847,-0.195521") or degrees with hemisphere letters
("36.35702° N, 5.24036° W"). The Create/Edit form also
accepts a What3Words address (``///filled.count.soap``), which the server
converts to decimal lat/lon at validation time via the What3Words REST
API — the stored value is always the decimal pair. When set, the map
places the location's marker at ``position`` instead of the astral-city
coordinates.
"""
import io
import json
import urllib.error

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from presence import what3words
from presence.forms import LocationForm
from presence.models import Location, format_lat_lon, parse_lat_lon

pytestmark = pytest.mark.django_db


# --- parsing & validation ---------------------------------------------------


def test_parse_lat_lon_valid_pair():
    assert parse_lat_lon("51.520847,-0.195521") == (51.520847, -0.195521)


def test_parse_lat_lon_allows_spaces():
    assert parse_lat_lon(" 51.5 , -0.12 ") == (51.5, -0.12)


@pytest.mark.parametrize(
    "value",
    [
        "junk",
        "51.5",
        "51.5,-0.1,7",
        "91,0",  # latitude out of range
        "-91,0",
        "0,181",  # longitude out of range
        "0,-181",
        "abc,def",
        "",
    ],
)
def test_parse_lat_lon_rejects_malformed_or_out_of_range(value):
    with pytest.raises(ValueError):
        parse_lat_lon(value)


@pytest.mark.parametrize(
    "value, expected",
    [
        # decimal degrees with a hemisphere letter (the sign)
        ("36.35702° N, 5.24036° W", (36.35702, -5.24036)),
        ("36.35702° S, 5.24036° E", (-36.35702, 5.24036)),
        # symbol and spacing are optional; letters are case-insensitive
        ("36.35702N,5.24036W", (36.35702, -5.24036)),
        ("36.35702 n , 5.24036 w", (36.35702, -5.24036)),
        # a bare degree symbol on a signed decimal is tolerated too
        ("51.520847°, -0.195521°", (51.520847, -0.195521)),
    ],
)
def test_parse_lat_lon_accepts_degrees_with_hemisphere(value, expected):
    assert parse_lat_lon(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "36.35702° E, 5.24036° W",  # E/W on the latitude
        "36.35702° N, 5.24036° S",  # N/S on the longitude
        "-36.35702° N, 5.24036° W",  # sign and hemisphere letter clash
        "91° N, 0° E",  # still range-checked
        "0° N, 181° W",
        "36.35702° X, 5.24036° W",  # unknown hemisphere letter
    ],
)
def test_parse_lat_lon_rejects_bad_hemisphere_input(value):
    with pytest.raises(ValueError):
        parse_lat_lon(value)


def test_format_lat_lon_strips_trailing_zeros():
    assert format_lat_lon(51.5, -0.12) == "51.5,-0.12"
    assert format_lat_lon(51.520847, -0.195521) == "51.520847,-0.195521"


def test_location_position_field_is_validated():
    location = Location(name="Office", timezone="UTC", position="not-a-pair")
    with pytest.raises(ValidationError) as exc:
        location.full_clean()
    assert "position" in exc.value.message_dict


def test_location_position_may_be_blank():
    Location(name="Office", timezone="UTC", position="").full_clean()


# --- coordinates prefer the position ----------------------------------------


def test_coordinates_prefer_position_over_city():
    location = Location(
        name="Office",
        timezone="Europe/London",
        city="London",
        position="10.5,20.25",
    )
    assert location.coordinates == (10.5, 20.25)


def test_coordinates_from_position_without_city():
    location = Location(name="Shed", timezone="UTC", position="10.5,20.25")
    assert location.coordinates == (10.5, 20.25)


def test_coordinates_fall_back_to_city_without_position():
    location = Location(name="Office", timezone="Europe/London", city="London")
    latitude, longitude = location.coordinates
    assert latitude == pytest.approx(51.47, abs=0.1)


def test_coordinates_none_without_position_or_city():
    assert Location(name="Nowhere", timezone="UTC").coordinates is None


# --- What3Words recognition & conversion ------------------------------------


@pytest.mark.parametrize(
    "value",
    ["///filled.count.soap", "filled.count.soap", "/filled.count.soap"],
)
def test_looks_like_what3words_accepts_three_words(value):
    assert what3words.looks_like_what3words(value)


@pytest.mark.parametrize(
    "value",
    ["51.5,-0.12", "filled.count", "filled.count.soap.extra", "a.1.b", ""],
)
def test_looks_like_what3words_rejects_other_input(value):
    assert not what3words.looks_like_what3words(value)


def _fake_urlopen_returning(payload: dict):
    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _urlopen(url, timeout=None):
        return _Response(json.dumps(payload).encode())

    return _urlopen


def test_convert_to_coordinates_success(settings, monkeypatch):
    settings.W3W_API_KEY = "test-key"
    monkeypatch.setattr(
        what3words,
        "urlopen",
        _fake_urlopen_returning(
            {"coordinates": {"lat": 51.520847, "lng": -0.195521}}
        ),
    )

    assert what3words.convert_to_coordinates("///filled.count.soap") == (
        51.520847,
        -0.195521,
    )


def test_convert_to_coordinates_requires_api_key(settings):
    settings.W3W_API_KEY = ""
    with pytest.raises(what3words.What3WordsError, match="not configured"):
        what3words.convert_to_coordinates("///filled.count.soap")


def test_convert_to_coordinates_rejected_address(settings, monkeypatch):
    settings.W3W_API_KEY = "test-key"
    body = json.dumps(
        {"error": {"code": "BadWords", "message": "words not recognised"}}
    ).encode()

    def _urlopen(url, timeout=None):
        raise urllib.error.HTTPError(
            url, 400, "Bad Request", hdrs=None, fp=io.BytesIO(body)
        )

    monkeypatch.setattr(what3words, "urlopen", _urlopen)

    with pytest.raises(what3words.What3WordsError, match="words not recognised"):
        what3words.convert_to_coordinates("///no.such.words")


def test_convert_to_coordinates_network_failure(settings, monkeypatch):
    settings.W3W_API_KEY = "test-key"

    def _urlopen(url, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(what3words, "urlopen", _urlopen)

    with pytest.raises(what3words.What3WordsError, match="lookup failed"):
        what3words.convert_to_coordinates("///filled.count.soap")


# --- the Create/Edit form ---------------------------------------------------


def _form_data(**overrides):
    data = {"name": "Office", "timezone": "UTC", "city": "", "position": ""}
    data.update(overrides)
    return data


def test_form_accepts_and_normalizes_a_lat_lon_pair():
    form = LocationForm(data=_form_data(position=" 51.5 , -0.12 "))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["position"] == "51.5,-0.12"


def test_form_normalizes_degrees_with_hemisphere_to_decimal():
    form = LocationForm(data=_form_data(position="36.35702° N, 5.24036° W"))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["position"] == "36.35702,-5.24036"


def test_form_allows_blank_position():
    form = LocationForm(data=_form_data(position=""))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["position"] == ""


def test_form_converts_a_what3words_address(monkeypatch):
    monkeypatch.setattr(
        what3words,
        "convert_to_coordinates",
        lambda words: (51.520847, -0.195521),
    )

    form = LocationForm(data=_form_data(position="///filled.count.soap"))

    assert form.is_valid(), form.errors
    assert form.cleaned_data["position"] == "51.520847,-0.195521"


def test_form_surfaces_what3words_errors_on_the_position_field(monkeypatch):
    def _fail(words):
        raise what3words.What3WordsError("words not recognised")

    monkeypatch.setattr(what3words, "convert_to_coordinates", _fail)

    form = LocationForm(data=_form_data(position="///no.such.words"))

    assert not form.is_valid()
    assert "words not recognised" in str(form.errors["position"])


def test_form_rejects_what3words_when_key_unconfigured(settings):
    # No monkeypatching: the real converter refuses before any network I/O.
    settings.W3W_API_KEY = ""

    form = LocationForm(data=_form_data(position="///filled.count.soap"))

    assert not form.is_valid()
    assert "not configured" in str(form.errors["position"])


def test_form_rejects_malformed_position():
    form = LocationForm(data=_form_data(position="somewhere nice"))
    assert not form.is_valid()
    assert "position" in form.errors


# --- the map uses the position ----------------------------------------------


def _login(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)
    return user


def test_map_plots_location_with_position_but_no_city(client, django_user_model):
    _login(client, django_user_model)
    Location.objects.create(name="Shed", timezone="UTC", position="10.5,20.25")

    plotted = client.get(reverse("map")).context["plotted"]

    (entry,) = [e for e in plotted if e["name"] == "Shed"]
    assert entry["latitude"] == 10.5
    assert entry["longitude"] == 20.25


def test_map_position_overrides_city_coordinates(client, django_user_model):
    _login(client, django_user_model)
    Location.objects.create(
        name="Office",
        timezone="Europe/London",
        city="London",
        position="10.5,20.25",
    )

    plotted = client.get(reverse("map")).context["plotted"]

    (entry,) = [e for e in plotted if e["name"] == "Office"]
    assert entry["latitude"] == 10.5
    assert entry["longitude"] == 20.25


# --- the detail page shows the stored pair ----------------------------------


def test_location_detail_shows_position(client, django_user_model):
    _login(client, django_user_model)
    location = Location.objects.create(
        name="Office", timezone="UTC", position="51.520847,-0.195521"
    )

    response = client.get(reverse("location_detail", args=[location.pk]))

    assert "51.520847,-0.195521" in response.content.decode()
