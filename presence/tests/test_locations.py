"""Tests for locations (issue #33).

Covers the :class:`~presence.models.Location` model, the location CRUD views,
the migration-seeded ``Default`` location, the location filter on the presence
and access-key list screens, and the presence form defaulting to ``Default``.
"""
import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.urls import reverse

from presence.models import DEFAULT_LOCATION_NAME, AccessKey, Location, Presence

pytestmark = pytest.mark.django_db


# --- model ---------------------------------------------------------------


def test_name_must_be_unique():
    Location.objects.create(name="Dup")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Location.objects.create(name="Dup")


def test_str_returns_name():
    assert str(Location(name="Office")) == "Office"


def test_in_use_reflects_links(make_presence, location):
    assert location.in_use is False
    make_presence(location=location).save()
    assert location.in_use is True


def test_is_default_only_for_default_name():
    assert Location(name=DEFAULT_LOCATION_NAME).is_default is True
    assert Location(name="Office").is_default is False


def test_protect_blocks_deleting_location_in_use(make_presence, location):
    make_presence(location=location).save()
    with pytest.raises(ProtectedError):
        with transaction.atomic():
            location.delete()


def test_default_location_is_seeded():
    # Migration 0011 creates the Default location on every database.
    assert Location.objects.filter(name=DEFAULT_LOCATION_NAME).exists()


# --- location views ------------------------------------------------------


def _login(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)
    return user


def test_index_redirects_anonymous_to_login(client):
    response = client.get(reverse("location_index"))
    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_index_lists_locations(client, django_user_model):
    _login(client, django_user_model)
    Location.objects.create(name="Office")
    Location.objects.create(name="Workshop")

    body = client.get(reverse("location_index")).content.decode()

    assert "Office" in body
    assert "Workshop" in body


def test_detail_shows_presences_and_access_keys(
    client, django_user_model, make_presence, location, access_key
):
    _login(client, django_user_model)
    make_presence(
        identifier="lamp", name="Lamp", location=location, access_key=access_key
    ).save()

    body = client.get(reverse("location_detail", args=[location.pk])).content.decode()

    # The linked presence and the access key it uses are both listed.
    assert "Lamp" in body
    assert reverse("detail", args=["lamp"]) in body
    assert access_key.name in body


def test_add_renders_form(client, django_user_model):
    _login(client, django_user_model)
    response = client.get(reverse("location_add"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "<form" in body
    assert 'name="name"' in body


def test_add_creates_location(client, django_user_model):
    _login(client, django_user_model)

    response = client.post(reverse("location_add"), {"name": "New Place"})

    location = Location.objects.get(name="New Place")
    assert response.status_code == 302
    assert response["Location"] == reverse("location_detail", args=[location.pk])


def test_edit_renames_location(client, django_user_model, location):
    _login(client, django_user_model)

    response = client.post(reverse("location_edit", args=[location.pk]), {"name": "Renamed"})

    assert response.status_code == 302
    location.refresh_from_db()
    assert location.name == "Renamed"


def test_edit_refuses_renaming_default(client, django_user_model):
    _login(client, django_user_model)
    default = Location.objects.get(name=DEFAULT_LOCATION_NAME)

    response = client.post(
        reverse("location_edit", args=[default.pk]), {"name": "Somewhere"}
    )

    # The Default location must stay findable, so its name is locked.
    assert response.status_code == 302
    default.refresh_from_db()
    assert default.name == DEFAULT_LOCATION_NAME


def test_delete_removes_unused_location(client, django_user_model, location):
    _login(client, django_user_model)

    response = client.post(reverse("location_delete", args=[location.pk]))

    assert response.status_code == 302
    assert response["Location"] == reverse("location_index")
    assert not Location.objects.filter(pk=location.pk).exists()


def test_delete_refused_while_in_use(client, django_user_model, make_presence, location):
    _login(client, django_user_model)
    make_presence(location=location).save()

    response = client.post(reverse("location_delete", args=[location.pk]))

    assert response.status_code == 302
    assert response["Location"] == reverse("location_detail", args=[location.pk])
    assert Location.objects.filter(pk=location.pk).exists()


def test_delete_refused_for_default(client, django_user_model):
    _login(client, django_user_model)
    default = Location.objects.get(name=DEFAULT_LOCATION_NAME)

    response = client.post(reverse("location_delete", args=[default.pk]))

    assert response.status_code == 302
    assert Location.objects.filter(pk=default.pk).exists()


def test_delete_get_not_allowed(client, django_user_model, location):
    _login(client, django_user_model)
    response = client.get(reverse("location_delete", args=[location.pk]))
    assert response.status_code == 405
    assert Location.objects.filter(pk=location.pk).exists()


# --- location filtering on list screens ----------------------------------


def test_presence_index_filters_by_location(client, django_user_model, make_presence):
    _login(client, django_user_model)
    office = Location.objects.create(name="Office")
    workshop = Location.objects.create(name="Workshop")
    make_presence(identifier="desk-lamp", name="Desk Lamp", location=office).save()
    make_presence(identifier="drill", name="Drill", location=workshop).save()

    body = client.get(reverse("index"), {"location": office.pk}).content.decode()

    assert "Desk Lamp" in body
    assert "Drill" not in body


def test_presence_index_ignores_bad_location_param(client, django_user_model, make_presence):
    _login(client, django_user_model)
    make_presence(identifier="desk-lamp", name="Desk Lamp").save()

    # A non-integer / unknown filter falls back to showing everything.
    body = client.get(reverse("index"), {"location": "not-a-number"}).content.decode()
    assert "Desk Lamp" in body
    body = client.get(reverse("index"), {"location": "999999"}).content.decode()
    assert "Desk Lamp" in body


def test_access_key_index_filters_by_location(client, django_user_model, make_presence):
    _login(client, django_user_model)
    office = Location.objects.create(name="Office")
    workshop = Location.objects.create(name="Workshop")
    office_key = AccessKey.objects.create(name="Office Key")
    workshop_key = AccessKey.objects.create(name="Workshop Key")
    make_presence(
        identifier="desk-lamp", location=office, access_key=office_key
    ).save()
    make_presence(
        identifier="drill", location=workshop, access_key=workshop_key
    ).save()

    body = client.get(reverse("access_key_index"), {"location": office.pk}).content.decode()

    assert "Office Key" in body
    assert "Workshop Key" not in body


def test_access_key_index_dedupes_keys_used_across_presences(
    client, django_user_model, make_presence
):
    _login(client, django_user_model)
    office = Location.objects.create(name="Office")
    shared = AccessKey.objects.create(name="Shared Key")
    make_presence(identifier="lamp-a", location=office, access_key=shared).save()
    make_presence(identifier="lamp-b", location=office, access_key=shared).save()

    body = client.get(reverse("access_key_index"), {"location": office.pk}).content.decode()

    # A key used by two presences at the same location is listed once.
    assert body.count(reverse("access_key_detail", args=[shared.pk])) == 1


# --- presence form defaults to the Default location ----------------------


_PRESENCE_PAYLOAD = {
    "identifier": "desk-lamp",
    "name": "Desk Lamp",
    "enabled": "on",
    "timezone": "UTC",
    "earliest_on": "20:00",
    "latest_off": "23:00",
    "earliest_on_offset": "",
    "latest_off_offset": "",
    "city": "",
    "min_on_duration": "01:00:00",
    "max_on_duration": "02:00:00",
    "min_off_duration": "01:00:00",
    "max_off_duration": "02:00:00",
}


def test_add_form_preselects_default_location(client, django_user_model):
    _login(client, django_user_model)
    default = Location.objects.get(name=DEFAULT_LOCATION_NAME)

    response = client.get(reverse("add"))

    assert response.status_code == 200
    assert response.context["form"].fields["location"].initial == default


def test_add_presence_defaults_to_default_location(
    client, django_user_model, access_key
):
    _login(client, django_user_model)
    default = Location.objects.get(name=DEFAULT_LOCATION_NAME)

    payload = {**_PRESENCE_PAYLOAD, "access_key": access_key.pk}
    response = client.post(reverse("add"), payload)

    assert response.status_code == 302
    assert Presence.objects.get(identifier="desk-lamp").location == default


def test_add_presence_links_selected_location(
    client, django_user_model, access_key
):
    _login(client, django_user_model)
    office = Location.objects.create(name="Office")

    payload = {**_PRESENCE_PAYLOAD, "access_key": access_key.pk, "location": office.pk}
    response = client.post(reverse("add"), payload)

    assert response.status_code == 302
    assert Presence.objects.get(identifier="desk-lamp").location == office
