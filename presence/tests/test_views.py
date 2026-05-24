"""Tests for the root status page (`presence.views.index`).

The page is login-gated and lists every Presence with an on/off LED and an
enabled indicator. These tests exercise the auth redirect and the rendered
contents; they hit the DB and the test client, so they are marked
``django_db`` (unlike the model suite, which uses unsaved instances).
"""
import pytest
from django.urls import reverse

from .conftest import VALID_KWARGS

pytestmark = pytest.mark.django_db


def test_index_redirects_anonymous_to_admin_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert reverse("admin:login") in response["Location"]


def test_index_lists_presence_for_logged_in_user(client, django_user_model, make_presence):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence(current_state="on").save()

    client.force_login(user)
    response = client.get("/")

    assert response.status_code == 200
    body = response.content.decode()
    assert VALID_KWARGS["name"] in body
    # The on-state LED carries the `on` class.
    assert 'class="led on"' in body


def test_index_marks_disabled_row(client, django_user_model, make_presence):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence(enabled=False).save()

    client.force_login(user)
    response = client.get("/")

    assert response.status_code == 200
    assert "disabled" in response.content.decode()
