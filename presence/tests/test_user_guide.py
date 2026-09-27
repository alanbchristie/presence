"""Tests for the User guide page and its menu item (issue #85)."""
import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)
    return user


def test_guide_requires_login(client):
    response = client.get(reverse("user_guide"))

    assert response.status_code == 302
    assert response.url.startswith(reverse("login"))


def test_guide_covers_the_app_and_each_record_type(client, staff):
    response = client.get(reverse("user_guide"))

    assert response.status_code == 200
    headings = re.findall(r"<h2[^>]*>([^<]+)</h2>", response.content.decode())
    assert headings == [
        "What it is for",
        "Locations",
        "Access keys",
        "Presences",
        "Using a presence",
        "Home Assistant",
        "Under the hood",
    ]


def test_guide_advises_a_unique_key_per_presence(client, staff):
    html = client.get(reverse("user_guide")).content.decode()

    assert "Ideally, give each presence its own access key" in html
    assert "presences at the same location can share a key" in html


def test_menu_links_to_the_guide(client, staff):
    html = client.get(reverse("index")).content.decode()

    assert f'<a class="nav-link" href="{reverse("user_guide")}">User guide</a>' in html


def test_menu_hides_the_guide_when_logged_out(client):
    html = client.get(reverse("login")).content.decode()

    assert "User guide" not in html


def test_guide_shows_a_home_assistant_automation(client, staff):
    html = client.get(reverse("user_guide")).content.decode()

    # The sensor's entity id follows from the name the HA snippet gives it.
    assert "binary_sensor.presence_simulator_living_room" in html
    assert "- trigger: state" in html
    assert "action: light.turn_on" in html


def test_guide_pairs_on_and_off_automations_with_an_override(client, staff):
    """The example follows a real deployment: separate "On" and "Off"
    automations, each guarded by a per-light manual override toggle."""
    html = client.get(reverse("user_guide")).content.decode()

    assert "alias: Living room lamp (On) [Living room simulator]" in html
    assert "alias: Living room lamp (Off) [Living room simulator]" in html
    assert "action: light.turn_off" in html
    assert html.count("entity_id: input_boolean.living_room_override") == 2
    assert "brightness_pct: 40" in html
