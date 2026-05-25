"""Tests for the root status page and the login/logout flow.

The root page (`presence.views.index`) is login-gated and lists every Presence
with a Bootstrap pill badge for its on/off state and one for its enabled flag.
Login/logout use Django's built-in ``LoginView`` / ``LogoutView`` wired in
``presence_site.urls``. These tests hit the DB and the test client, so they are
marked ``django_db`` (unlike the model suite, which uses unsaved instances).
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
    # Enabled + on → green (Success) state pill and a green Enabled pill.
    assert "text-bg-success" in body
    assert "badge rounded-pill" in body


def test_index_off_state_uses_danger_badge(client, django_user_model, make_presence):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence(enabled=True, current_state="off").save()

    client.force_login(user)
    body = client.get("/").content.decode()

    # Enabled + off → red (Danger) state pill.
    assert '<span class="badge rounded-pill text-bg-danger">Off</span>' in body


def test_index_marks_disabled_row(client, django_user_model, make_presence):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence(enabled=False, current_state="on").save()

    client.force_login(user)
    response = client.get("/")

    assert response.status_code == 200
    body = response.content.decode()
    # A disabled record's state pill is grey (Secondary) regardless of on/off,
    # and its enabled pill is red (Danger).
    assert "text-bg-secondary" in body
    assert '<span class="badge rounded-pill text-bg-danger">Disabled</span>' in body


def test_index_shows_username_and_logout_when_authenticated(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")

    client.force_login(user)
    response = client.get("/")

    body = response.content.decode()
    assert "staff" in body
    # NavBar brand plus a POST logout control.
    assert 'class="navbar-brand"' in body
    assert f'action="{reverse("logout")}"' in body


def test_login_page_shows_navbar_without_logout(client):
    body = client.get(reverse("login")).content.decode()

    # The NavBar (and its Presence brand) appears on every page...
    assert 'class="navbar-brand"' in body
    # ...but the logout control only shows once authenticated.
    assert f'action="{reverse("logout")}"' not in body


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


def test_about_modal_with_version_visible_to_anonymous(client, settings):
    settings.VERSION = "9.9.9-test"

    # The About control + modal must be reachable without logging in, so check
    # the (anonymous) login page.
    body = client.get(reverse("login")).content.decode()

    assert 'data-bs-target="#aboutModal"' in body  # NavBar trigger
    assert 'id="aboutModal"' in body  # the modal itself
    assert "9.9.9-test" in body  # the running version


def test_version_available_in_template_context(client, settings):
    settings.VERSION = "1.2.3"

    response = client.get(reverse("login"))

    assert response.context["version"] == "1.2.3"
