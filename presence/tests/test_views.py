"""Tests for the root status page and the login/logout flow.

The root page (`presence.views.index`) is login-gated and lists every Presence
with a Bootstrap pill badge for its on/off state and one for its enabled flag.
Login/logout use Django's built-in ``LoginView`` / ``LogoutView`` wired in
``presence_site.urls``. These tests hit the DB and the test client, so they are
marked ``django_db`` (unlike the model suite, which uses unsaved instances).
"""
import pytest
from django.urls import reverse

from presence.models import Presence

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


def test_index_identifier_links_to_detail_page(client, django_user_model, make_presence):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence().save()

    client.force_login(user)
    body = client.get("/").content.decode()

    # The identifier links to the HTML detail page, not the JSON API endpoint.
    assert f'href="{reverse("detail", args=[VALID_KWARGS["identifier"]])}"' in body


def test_detail_redirects_anonymous_to_login(client, make_presence):
    make_presence().save()
    url = reverse("detail", args=[VALID_KWARGS["identifier"]])

    response = client.get(url)

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_detail_shows_record_for_logged_in_user(client, django_user_model, make_presence):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence(current_state="on").save()
    client.force_login(user)

    body = client.get(reverse("detail", args=[VALID_KWARGS["identifier"]])).content.decode()

    assert VALID_KWARGS["name"] in body
    assert VALID_KWARGS["identifier"] in body
    # On + enabled → a green state pill on the detail page too.
    assert "text-bg-success" in body


def test_detail_unknown_identifier_returns_404(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)

    response = client.get(reverse("detail", args=["does-not-exist"]))

    assert response.status_code == 404


def test_detail_shows_delete_button_and_confirm_modal(client, django_user_model, make_presence):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence().save()
    client.force_login(user)

    body = client.get(reverse("detail", args=[VALID_KWARGS["identifier"]])).content.decode()

    # A red Delete button opens the confirmation modal, which POSTs to the
    # delete endpoint and offers Cancel / Delete controls.
    delete_url = reverse("delete", args=[VALID_KWARGS["identifier"]])
    assert 'data-bs-target="#deleteModal"' in body
    assert 'id="deleteModal"' in body
    assert 'class="btn btn-danger"' in body
    assert f'action="{delete_url}"' in body


def test_detail_shows_edit_button(client, django_user_model, make_presence):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence().save()
    client.force_login(user)

    body = client.get(reverse("detail", args=[VALID_KWARGS["identifier"]])).content.decode()

    # An Edit button links to the edit page for this record.
    assert f'href="{reverse("edit", args=[VALID_KWARGS["identifier"]])}"' in body


#: A valid full POST payload mirroring make_presence()'s absolute-window record.
EDIT_POST_DATA = {
    "identifier": "lamp",
    "name": "Lamp Renamed",
    "enabled": "on",
    "timezone": "UTC",
    "earliest_on": "20:00",
    "latest_off": "23:00",
    "earliest_on_offset": "",
    "latest_off_offset": "",
    "city": "",
    "min_on_duration": "01:00:00",
    "max_on_duration": "01:00:00",
    "min_off_duration": "01:00:00",
    "max_off_duration": "01:00:00",
}


def test_edit_redirects_anonymous_to_login(client, make_presence):
    make_presence().save()
    response = client.get(reverse("edit", args=[VALID_KWARGS["identifier"]]))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_edit_unknown_identifier_returns_404(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)

    response = client.get(reverse("edit", args=["does-not-exist"]))

    assert response.status_code == 404


def test_edit_renders_prepopulated_form(client, django_user_model, make_presence):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence().save()
    client.force_login(user)

    body = client.get(reverse("edit", args=[VALID_KWARGS["identifier"]])).content.decode()

    # The form is bound to the existing record: its current values are present.
    assert f'value="{VALID_KWARGS["identifier"]}"' in body
    assert f'value="{VALID_KWARGS["name"]}"' in body


def test_edit_saves_changes_and_redirects_to_detail(client, django_user_model, make_presence, access_key):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence().save()
    client.force_login(user)

    payload = {**EDIT_POST_DATA, "access_key": access_key.pk}
    response = client.post(reverse("edit", args=[VALID_KWARGS["identifier"]]), payload)

    assert response.status_code == 302
    assert response["Location"] == reverse("detail", args=[VALID_KWARGS["identifier"]])
    refreshed = Presence.objects.get(identifier=VALID_KWARGS["identifier"])
    assert refreshed.name == "Lamp Renamed"


def test_edit_does_not_create_a_second_record(client, django_user_model, make_presence, access_key):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence().save()
    client.force_login(user)

    payload = {**EDIT_POST_DATA, "access_key": access_key.pk}
    client.post(reverse("edit", args=[VALID_KWARGS["identifier"]]), payload)

    # Editing mutates the existing row rather than inserting a new one.
    assert Presence.objects.count() == 1


def test_edit_can_change_identifier_and_redirects_to_new_detail(client, django_user_model, make_presence, access_key):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence().save()
    client.force_login(user)

    payload = {**EDIT_POST_DATA, "access_key": access_key.pk, "identifier": "lamp-2"}
    response = client.post(reverse("edit", args=[VALID_KWARGS["identifier"]]), payload)

    assert response.status_code == 302
    # The redirect targets the record's new identifier, not the old one.
    assert response["Location"] == reverse("detail", args=["lamp-2"])
    assert Presence.objects.filter(identifier="lamp-2").exists()
    assert not Presence.objects.filter(identifier="lamp").exists()


def test_edit_invalid_re_renders_with_errors(client, django_user_model, make_presence):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence().save()
    client.force_login(user)

    # An invalid identifier (uppercase breaks the DNS-label rule).
    bad = {**EDIT_POST_DATA, "identifier": "Not Valid"}
    response = client.post(reverse("edit", args=[VALID_KWARGS["identifier"]]), bad)

    assert response.status_code == 200
    assert response.context["form"].errors
    # The original record is unchanged.
    assert Presence.objects.get(identifier="lamp").name == VALID_KWARGS["name"]


def test_delete_redirects_anonymous_to_login(client, make_presence):
    make_presence().save()
    url = reverse("delete", args=[VALID_KWARGS["identifier"]])

    response = client.post(url)

    assert response.status_code == 302
    assert reverse("login") in response["Location"]
    # The record survives an unauthenticated attempt.
    assert Presence.objects.filter(identifier=VALID_KWARGS["identifier"]).exists()


def test_delete_get_not_allowed(client, django_user_model, make_presence):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence().save()
    client.force_login(user)

    response = client.get(reverse("delete", args=[VALID_KWARGS["identifier"]]))

    # Deletion must not happen on a GET; the record is left intact.
    assert response.status_code == 405
    assert Presence.objects.filter(identifier=VALID_KWARGS["identifier"]).exists()


def test_delete_removes_record_and_redirects_to_root(client, django_user_model, make_presence):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    make_presence().save()
    client.force_login(user)

    response = client.post(reverse("delete", args=[VALID_KWARGS["identifier"]]))

    assert response.status_code == 302
    assert response["Location"] == reverse("index")
    assert not Presence.objects.filter(identifier=VALID_KWARGS["identifier"]).exists()


def test_delete_unknown_identifier_returns_404(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)

    response = client.post(reverse("delete", args=["does-not-exist"]))

    assert response.status_code == 404


#: A valid POST payload for the Add form (absolute window mode, fresh identifier).
ADD_POST_DATA = {
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


def test_index_shows_add_button_for_logged_in_user(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)

    body = client.get("/").content.decode()

    # An Add button links to the add page (shown even with no records).
    assert f'href="{reverse("add")}"' in body
    assert "btn btn-primary" in body


def test_add_redirects_anonymous_to_login(client):
    response = client.get(reverse("add"))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_add_renders_form_for_logged_in_user(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)

    response = client.get(reverse("add"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "<form" in body
    # The form exposes the editable identifier but not the runner-managed state.
    assert 'name="identifier"' in body
    assert 'name="current_state"' not in body


def test_add_creates_record_and_redirects_to_root(client, django_user_model, access_key):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)

    payload = {**ADD_POST_DATA, "access_key": access_key.pk}
    response = client.post(reverse("add"), payload)

    assert response.status_code == 302
    assert response["Location"] == reverse("index")
    created = Presence.objects.get(identifier="desk-lamp")
    assert created.name == "Desk Lamp"
    assert created.access_key == access_key


def test_add_invalid_re_renders_with_errors_and_creates_nothing(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)

    # Missing the required name field.
    bad = {**ADD_POST_DATA, "name": ""}
    response = client.post(reverse("add"), bad)

    assert response.status_code == 200
    assert response.context["form"].errors
    assert not Presence.objects.filter(identifier="desk-lamp").exists()


def test_add_get_does_not_create(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)

    client.get(reverse("add"))

    assert not Presence.objects.exists()


def test_index_shows_username_and_logout_when_authenticated(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")

    client.force_login(user)
    response = client.get("/")

    body = response.content.decode()
    assert "staff" in body
    # NavBar brand plus a POST logout control.
    assert 'class="navbar-brand"' in body
    assert f'action="{reverse("logout")}"' in body


def test_navbar_account_menu_when_authenticated(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)

    body = client.get("/").content.decode()

    # The Account dropdown holds the username and a Logout control...
    assert 'class="nav-item dropdown"' in body
    assert ">Account</a>" in body
    assert "staff" in body
    assert f'action="{reverse("logout")}"' in body
    # ...but not a Login link when already authenticated.
    assert f'href="{reverse("login")}"' not in body


def test_navbar_account_menu_when_anonymous(client):
    body = client.get(reverse("login")).content.decode()

    # The Account dropdown offers a Login link to anonymous visitors...
    assert 'class="nav-item dropdown"' in body
    assert ">Account</a>" in body
    assert f'href="{reverse("login")}"' in body
    # ...but no logout control until authenticated.
    assert f'action="{reverse("logout")}"' not in body


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
