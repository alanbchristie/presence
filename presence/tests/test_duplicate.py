"""Tests for the "Duplicate" feature on Presence and Location detail pages.

Duplicating opens a create form pre-filled from the source record so the user
can give the copy a new name (and, for a presence, choose its location) without
re-entering every field. Saving creates a brand new row; the source is left
untouched (issue #40).
"""
import pytest
from django.urls import reverse

from presence.models import Location, Presence

from .conftest import VALID_KWARGS

pytestmark = pytest.mark.django_db


def _login(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)
    return user


# --- presence duplicate --------------------------------------------------


def test_presence_duplicate_requires_login(client, make_presence):
    make_presence().save()
    url = reverse("duplicate", args=[VALID_KWARGS["identifier"]])

    response = client.get(url)

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_presence_duplicate_get_prefills_from_source(
    client, django_user_model, make_presence, location
):
    _login(client, django_user_model)
    make_presence().save()
    url = reverse("duplicate", args=[VALID_KWARGS["identifier"]])

    response = client.get(url)

    assert response.status_code == 200
    initial = response.context["form"].initial
    # Config fields are copied so the user can tweak rather than retype.
    assert initial["name"] == VALID_KWARGS["name"]
    assert initial["location"] == location.pk
    # The unique identifier is left blank: the user must supply a fresh one.
    assert not initial.get("identifier")


def test_presence_duplicate_post_creates_new_row(
    client, django_user_model, make_presence, access_key, location
):
    _login(client, django_user_model)
    source = make_presence()
    source.save()
    url = reverse("duplicate", args=[VALID_KWARGS["identifier"]])

    data = {
        "identifier": "lamp-copy",
        "name": "Lamp copy",
        "enabled": "on",
        "location": location.pk,
        "access_key": access_key.pk,
        "earliest_on": "20:00",
        "latest_off": "23:00",
        "min_on_duration": "01:00:00",
        "max_on_duration": "01:00:00",
        "min_off_duration": "01:00:00",
        "max_off_duration": "01:00:00",
    }
    response = client.post(url, data)

    assert response.status_code == 302
    assert response["Location"] == reverse("detail", args=["lamp-copy"])
    # A new row exists alongside the untouched source.
    assert Presence.objects.count() == 2
    copy = Presence.objects.get(identifier="lamp-copy")
    assert copy.pk != source.pk
    assert copy.name == "Lamp copy"
    assert copy.access_key == access_key
    # Timezone now comes from the linked location (issue #43).
    assert copy.location == location
    assert copy.location.timezone == "UTC"
    assert Presence.objects.filter(identifier=VALID_KWARGS["identifier"]).exists()


def test_presence_detail_has_duplicate_link(
    client, django_user_model, make_presence
):
    _login(client, django_user_model)
    make_presence().save()

    body = client.get(reverse("detail", args=[VALID_KWARGS["identifier"]])).content.decode()

    assert reverse("duplicate", args=[VALID_KWARGS["identifier"]]) in body


# --- location duplicate --------------------------------------------------


def test_location_duplicate_requires_login(client, location):
    response = client.get(reverse("location_duplicate", args=[location.pk]))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_location_duplicate_get_renders_blank_name(
    client, django_user_model, location
):
    _login(client, django_user_model)

    response = client.get(reverse("location_duplicate", args=[location.pk]))

    assert response.status_code == 200
    # The name is unique, so it is left blank for the user to supply a new one.
    assert not response.context["form"].initial.get("name")


def test_location_duplicate_post_creates_new_row(
    client, django_user_model, location
):
    _login(client, django_user_model)

    response = client.post(
        reverse("location_duplicate", args=[location.pk]),
        {"name": "Test Location copy", "timezone": "UTC"},
    )

    assert response.status_code == 302
    new = Location.objects.get(name="Test Location copy")
    assert response["Location"] == reverse("location_detail", args=[new.pk])
    assert Location.objects.filter(name=location.name).exists()


def test_location_detail_has_duplicate_link(
    client, django_user_model, location
):
    _login(client, django_user_model)

    body = client.get(reverse("location_detail", args=[location.pk])).content.decode()

    assert reverse("location_duplicate", args=[location.pk]) in body
