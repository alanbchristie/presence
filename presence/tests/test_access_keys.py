"""Tests for access keys (issue #26).

Covers the :class:`~presence.models.AccessKey` model, the per-presence API
auth that replaced the global ``PRESENCE_API_KEY`` env var, the access-key CRUD
views, and the inline key-creation path on the presence form.
"""
import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.urls import reverse

from presence.models import AccessKey, Presence

from .conftest import VALID_KWARGS

pytestmark = pytest.mark.django_db


# --- model ---------------------------------------------------------------


def test_value_is_auto_generated():
    key = AccessKey.objects.create(name="Generated")
    assert key.value  # non-empty secret minted without the caller supplying one


def test_generated_values_are_distinct():
    a = AccessKey.objects.create(name="A")
    b = AccessKey.objects.create(name="B")
    assert a.value != b.value


def test_name_must_be_unique():
    AccessKey.objects.create(name="Dup")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AccessKey.objects.create(name="Dup")


def test_str_returns_name():
    assert str(AccessKey(name="Front door")) == "Front door"


def test_in_use_reflects_links(make_presence, access_key):
    assert access_key.in_use is False
    make_presence(access_key=access_key).save()
    assert access_key.in_use is True


def test_protect_blocks_deleting_key_in_use(make_presence, access_key):
    make_presence(access_key=access_key).save()
    with pytest.raises(ProtectedError):
        with transaction.atomic():
            access_key.delete()


# --- API auth (per-presence key) -----------------------------------------


def _api_url(identifier=VALID_KWARGS["identifier"]):
    return reverse("presence:detail", args=[identifier])


def test_api_rejects_missing_header(client, make_presence):
    make_presence().save()
    response = client.get(_api_url())
    assert response.status_code == 403


def test_api_rejects_wrong_key(client, make_presence):
    make_presence().save()
    response = client.get(_api_url(), HTTP_X_API_KEY="not-the-key")
    assert response.status_code == 403


def test_api_accepts_matching_key(client, make_presence, access_key):
    make_presence(access_key=access_key).save()
    response = client.get(_api_url(), HTTP_X_API_KEY=access_key.value)
    assert response.status_code == 200
    data = response.json()
    assert data["identifier"] == VALID_KWARGS["identifier"]
    # The key's name is exposed, never its secret value.
    assert data["access_key"] == access_key.name
    assert access_key.value not in response.content.decode()


def test_api_unknown_identifier_returns_403_not_404(client, db):
    # #6: an unknown identifier must respond identically to a known one with a
    # bad key, so unauthenticated callers cannot enumerate which exist.
    response = client.get(_api_url("does-not-exist"))
    assert response.status_code == 403


def test_api_throttles_repeated_failures(client, make_presence):
    # #7: brute-force attempts are blocked after a threshold of failures.
    from presence.views import API_FAIL_LIMIT

    make_presence().save()
    for _ in range(API_FAIL_LIMIT):
        assert client.get(_api_url(), HTTP_X_API_KEY="nope").status_code == 403
    # The next attempt is throttled regardless of the (still-wrong) key.
    assert client.get(_api_url(), HTTP_X_API_KEY="nope").status_code == 429


def test_api_success_resets_throttle(client, make_presence, access_key):
    from presence.views import API_FAIL_LIMIT

    make_presence(access_key=access_key).save()
    for _ in range(API_FAIL_LIMIT - 1):
        client.get(_api_url(), HTTP_X_API_KEY="nope")
    # A valid request clears the failure counter...
    assert client.get(_api_url(), HTTP_X_API_KEY=access_key.value).status_code == 200
    # ...so the next bad key is a plain 403, not an immediate 429.
    assert client.get(_api_url(), HTTP_X_API_KEY="nope").status_code == 403


# --- access-key views ----------------------------------------------------


def _login(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)
    return user


def test_index_redirects_anonymous_to_login(client):
    response = client.get(reverse("access_key_index"))
    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_index_lists_keys_with_usage(client, django_user_model, make_presence):
    _login(client, django_user_model)
    used = AccessKey.objects.create(name="Used")
    AccessKey.objects.create(name="Spare")
    make_presence(identifier="lamp", access_key=used).save()

    body = client.get(reverse("access_key_index")).content.decode()

    assert "Used" in body
    assert "Spare" in body
    # The spare key is flagged as unused.
    assert "Unused" in body


def test_detail_shows_value_and_linked_presences(client, django_user_model, make_presence, access_key):
    _login(client, django_user_model)
    make_presence(identifier="lamp", name="Lamp", access_key=access_key).save()

    body = client.get(reverse("access_key_detail", args=[access_key.pk])).content.decode()

    assert access_key.value in body  # the secret is visible to the owner
    assert "Lamp" in body            # the linked presence is listed
    assert reverse("detail", args=["lamp"]) in body


def test_add_renders_form(client, django_user_model):
    _login(client, django_user_model)
    response = client.get(reverse("access_key_add"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "<form" in body
    assert 'name="name"' in body
    # The secret value is generated, never entered.
    assert 'name="value"' not in body


def test_edit_renders_prepopulated_form(client, django_user_model, access_key):
    _login(client, django_user_model)
    body = client.get(reverse("access_key_edit", args=[access_key.pk])).content.decode()
    assert f'value="{access_key.name}"' in body


def test_add_creates_key_with_generated_value(client, django_user_model):
    _login(client, django_user_model)

    response = client.post(reverse("access_key_add"), {"name": "New Key"})

    key = AccessKey.objects.get(name="New Key")
    assert response.status_code == 302
    assert response["Location"] == reverse("access_key_detail", args=[key.pk])
    assert key.value  # auto-generated


def test_edit_renames_key(client, django_user_model, access_key):
    _login(client, django_user_model)

    response = client.post(reverse("access_key_edit", args=[access_key.pk]), {"name": "Renamed"})

    assert response.status_code == 302
    access_key.refresh_from_db()
    assert access_key.name == "Renamed"


def test_delete_removes_unused_key(client, django_user_model, access_key):
    _login(client, django_user_model)

    response = client.post(reverse("access_key_delete", args=[access_key.pk]))

    assert response.status_code == 302
    assert response["Location"] == reverse("access_key_index")
    assert not AccessKey.objects.filter(pk=access_key.pk).exists()


def test_delete_refused_while_in_use(client, django_user_model, make_presence, access_key):
    _login(client, django_user_model)
    make_presence(access_key=access_key).save()

    response = client.post(reverse("access_key_delete", args=[access_key.pk]))

    # Requirement #4: an in-use key must not be deletable.
    assert response.status_code == 302
    assert response["Location"] == reverse("access_key_detail", args=[access_key.pk])
    assert AccessKey.objects.filter(pk=access_key.pk).exists()


def test_delete_get_not_allowed(client, django_user_model, access_key):
    _login(client, django_user_model)
    response = client.get(reverse("access_key_delete", args=[access_key.pk]))
    assert response.status_code == 405
    assert AccessKey.objects.filter(pk=access_key.pk).exists()


# --- presence form: key selection / inline creation (requirement 5) ------


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


def test_add_presence_links_selected_key(client, django_user_model, access_key):
    _login(client, django_user_model)

    payload = {**_PRESENCE_PAYLOAD, "access_key": access_key.pk}
    response = client.post(reverse("add"), payload)

    assert response.status_code == 302
    assert Presence.objects.get(identifier="desk-lamp").access_key == access_key


def test_add_presence_creates_new_key_inline(client, django_user_model):
    _login(client, django_user_model)

    payload = {**_PRESENCE_PAYLOAD, "access_key": "", "new_access_key_name": "Inline Key"}
    response = client.post(reverse("add"), payload)

    assert response.status_code == 302
    key = AccessKey.objects.get(name="Inline Key")
    assert Presence.objects.get(identifier="desk-lamp").access_key == key


def test_add_presence_without_any_key_is_rejected(client, django_user_model):
    _login(client, django_user_model)

    payload = {**_PRESENCE_PAYLOAD, "access_key": "", "new_access_key_name": ""}
    response = client.post(reverse("add"), payload)

    assert response.status_code == 200
    assert response.context["form"].errors
    assert not Presence.objects.filter(identifier="desk-lamp").exists()


def test_add_presence_rejects_both_key_and_new_name(client, django_user_model, access_key):
    _login(client, django_user_model)

    payload = {
        **_PRESENCE_PAYLOAD,
        "access_key": access_key.pk,
        "new_access_key_name": "Also This",
    }
    response = client.post(reverse("add"), payload)

    assert response.status_code == 200
    assert response.context["form"].errors
    assert not Presence.objects.filter(identifier="desk-lamp").exists()
    assert not AccessKey.objects.filter(name="Also This").exists()


def test_inline_duplicate_key_name_is_rejected(client, django_user_model, access_key):
    _login(client, django_user_model)

    payload = {**_PRESENCE_PAYLOAD, "access_key": "", "new_access_key_name": access_key.name}
    response = client.post(reverse("add"), payload)

    assert response.status_code == 200
    assert response.context["form"].errors
    assert not Presence.objects.filter(identifier="desk-lamp").exists()
    # No duplicate key row was created.
    assert AccessKey.objects.filter(name=access_key.name).count() == 1
