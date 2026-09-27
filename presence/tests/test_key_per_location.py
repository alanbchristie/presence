"""Tests for confining each access key to one location (issue #87).

A key may protect any number of presences, but only at a single location:
``Presence.clean`` rejects a presence whose key is already used by a presence
at a different location. That one check covers creating, duplicating and
moving a presence, through the web form and the admin alike.
"""
import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from presence.models import AccessKey, Location

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)
    return user


@pytest.fixture
def elsewhere() -> Location:
    return Location.objects.create(name="Elsewhere", timezone="UTC")


# --- model ---------------------------------------------------------------


def test_key_used_at_another_location_is_rejected(make_presence, elsewhere):
    make_presence(identifier="a").save()
    presence = make_presence(identifier="b", location=elsewhere)

    with pytest.raises(ValidationError) as excinfo:
        presence.full_clean()

    assert "access_key" in excinfo.value.message_dict


def test_key_shared_at_the_same_location_is_allowed(make_presence):
    make_presence(identifier="a").save()

    make_presence(identifier="b").full_clean()


def test_moving_a_presence_away_from_its_keys_other_users_is_rejected(
    make_presence, elsewhere
):
    make_presence(identifier="a").save()
    moving = make_presence(identifier="b")
    moving.save()

    moving.location = elsewhere
    with pytest.raises(ValidationError) as excinfo:
        moving.full_clean()

    assert "access_key" in excinfo.value.message_dict


def test_moving_a_presence_with_a_key_of_its_own_is_allowed(make_presence, elsewhere):
    moving = make_presence(identifier="a")
    moving.save()

    moving.location = elsewhere
    moving.full_clean()


def test_key_location_is_that_of_its_presences(make_presence, access_key, location):
    make_presence(identifier="a").save()

    assert access_key.location == location


def test_unused_key_has_no_location():
    assert AccessKey.objects.create(name="Spare").location is None


# --- views ---------------------------------------------------------------


def test_duplicating_a_presence_to_another_location_needs_another_key(
    client, staff, make_presence, access_key, elsewhere
):
    source = make_presence(identifier="source")
    source.save()
    data = {
        "identifier": "copy",
        "name": "Copy",
        "enabled": "on",
        "min_on_duration": "01:00:00",
        "max_on_duration": "01:00:00",
        "min_off_duration": "01:00:00",
        "max_off_duration": "01:00:00",
        "window_open": "20:00",
        "window_close": "23:00",
        "location": elsewhere.pk,
        "access_key": access_key.pk,
    }

    response = client.post(reverse("duplicate", args=[source.identifier]), data)

    assert response.status_code == 200
    assert "access_key" in response.context["form"].errors
    assert not elsewhere.presences.exists()


def test_access_key_list_shows_each_keys_location(
    client, staff, make_presence, access_key, location
):
    make_presence(identifier="a").save()

    response = client.get(reverse("access_key_index"))
    html = response.content.decode()

    assert reverse("location_detail", args=[location.pk]) in html
    assert f">{location.name}</a>" in html
