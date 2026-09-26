"""Tests for ordering the list screens by location (issue #80).

Locations list by name with the special ``Default`` location last. The
presence and access-key lists follow that same location order: presences by
their location then name, access keys by the first (in that order) location
of the presences that use them, with unused keys at the end.
"""
import pytest
from django.urls import reverse

from presence.models import DEFAULT_LOCATION_NAME, AccessKey, Location

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)
    return user


@pytest.fixture
def default_location() -> Location:
    location, _ = Location.objects.get_or_create(name=DEFAULT_LOCATION_NAME)
    return location


@pytest.fixture
def places(default_location) -> dict[str, Location]:
    """Locations whose names sort either side of ``Default``."""
    return {
        "Attic": Location.objects.create(name="Attic", timezone="UTC"),
        "Zoo": Location.objects.create(name="Zoo", timezone="UTC"),
        "Default": default_location,
    }


def _names(objects) -> list[str]:
    return [o.name for o in objects]


def test_location_sort_key_puts_default_last():
    locations = [Location(name="Zoo"), Location(name=DEFAULT_LOCATION_NAME), Location(name="attic")]

    ordered = sorted(locations, key=lambda location: location.sort_key)

    assert _names(ordered) == ["attic", "Zoo", DEFAULT_LOCATION_NAME]


def test_locations_list_by_name_with_default_last(client, staff, places):
    response = client.get(reverse("location_index"))

    assert _names(response.context["locations"]) == ["Attic", "Zoo", DEFAULT_LOCATION_NAME]


def test_presences_list_by_location_then_name(client, staff, make_presence, places):
    make_presence(identifier="a", name="Alpha", location=places["Default"]).save()
    make_presence(identifier="b", name="Bravo", location=places["Zoo"]).save()
    make_presence(identifier="c", name="Charlie", location=places["Attic"]).save()
    make_presence(identifier="d", name="Delta", location=places["Zoo"]).save()

    response = client.get(reverse("index"))

    assert _names(response.context["presences"]) == ["Charlie", "Bravo", "Delta", "Alpha"]


def test_presences_location_filter_still_applies(client, staff, make_presence, places):
    make_presence(identifier="a", name="Alpha", location=places["Zoo"]).save()
    make_presence(identifier="b", name="Bravo", location=places["Attic"]).save()

    response = client.get(reverse("index"), {"location": places["Zoo"].pk})

    assert _names(response.context["presences"]) == ["Alpha"]


def test_access_keys_list_by_location_with_unused_last(client, staff, make_presence, places):
    at_default = AccessKey.objects.create(name="A at Default")
    at_zoo = AccessKey.objects.create(name="B at Zoo")
    # Used at Default and Attic: its earliest location (Attic) places it.
    at_attic = AccessKey.objects.create(name="C at Attic")
    unused = AccessKey.objects.create(name="D unused")
    make_presence(identifier="p1", access_key=at_default, location=places["Default"]).save()
    make_presence(identifier="p2", access_key=at_zoo, location=places["Zoo"]).save()
    make_presence(identifier="p3", access_key=at_attic, location=places["Default"]).save()
    make_presence(identifier="p4", access_key=at_attic, location=places["Attic"]).save()

    response = client.get(reverse("access_key_index"))

    names = _names(response.context["keys"])
    # The fixture's shared "Test Key" and the migration-seeded "Default" key
    # are unused here, so they sort by name among the unused keys at the end.
    assert names[:3] == [at_attic.name, at_zoo.name, at_default.name]
    assert names[3:] == sorted(names[3:], key=str.casefold)
    assert unused.name in names[3:]


def test_access_keys_same_location_list_by_name(client, staff, make_presence, places):
    second = AccessKey.objects.create(name="b key")
    first = AccessKey.objects.create(name="A key")
    make_presence(identifier="p1", access_key=second, location=places["Zoo"]).save()
    make_presence(identifier="p2", access_key=first, location=places["Zoo"]).save()

    response = client.get(reverse("access_key_index"))

    assert _names(response.context["keys"])[:2] == ["A key", "b key"]


def test_access_keys_location_filter_still_applies(client, staff, make_presence, places):
    at_zoo = AccessKey.objects.create(name="Zoo key")
    at_attic = AccessKey.objects.create(name="Attic key")
    make_presence(identifier="p1", access_key=at_zoo, location=places["Zoo"]).save()
    make_presence(identifier="p2", access_key=at_attic, location=places["Attic"]).save()

    response = client.get(reverse("access_key_index"), {"location": places["Zoo"].pk})

    assert _names(response.context["keys"]) == ["Zoo key"]
