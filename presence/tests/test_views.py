"""Tests for the root status page and the login/logout flow.

The root page (`presence.views.index`) is login-gated and lists every Presence
with an on/off LED and an enabled indicator. Login/logout use Django's built-in
``LoginView`` / ``LogoutView`` wired in ``presence_site.urls``. These tests hit
the DB and the test client, so they are marked ``django_db`` (unlike the model
suite, which uses unsaved instances).
"""
import pytest
from django.urls import reverse

from .conftest import VALID_KWARGS

pytestmark = pytest.mark.django_db


def test_index_redirects_anonymous_to_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert reverse("login") in response["Location"]


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


def test_index_shows_username_and_logout_when_authenticated(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")

    client.force_login(user)
    response = client.get("/")

    body = response.content.decode()
    assert "staff" in body
    assert f'action="{reverse("logout")}"' in body


def test_login_page_renders(client):
    response = client.get(reverse("login"))
    assert response.status_code == 200
    assert "<form" in response.content.decode()


def test_login_with_valid_credentials_redirects_to_index(client, django_user_model):
    django_user_model.objects.create_user(username="staff", password="pw")

    response = client.post(reverse("login"), {"username": "staff", "password": "pw"})

    assert response.status_code == 302
    assert response["Location"] == reverse("index")


def test_login_with_invalid_credentials_re_renders_with_error(client, django_user_model):
    django_user_model.objects.create_user(username="staff", password="pw")

    response = client.post(reverse("login"), {"username": "staff", "password": "wrong"})

    assert response.status_code == 200
    assert response.context["form"].errors


def test_logout_logs_out_and_redirects_to_login(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)

    response = client.post(reverse("logout"))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]
    # Subsequent access to the gated page bounces back to login.
    assert client.get("/").status_code == 302
