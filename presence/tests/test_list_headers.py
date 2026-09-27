"""Tests for the list screens' columns, header links and menu (issues #82-#84).

A count column's header names the list it counts and links to it: the access
keys' "Presences" and "Location", the presences' "Location" and the
locations' "Presences". The menu lists "Map" last. (A key's location column,
a count here in #82, names the key's one location since #87.)
"""
import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)
    return user


def _header_link(html: str, text: str) -> str | None:
    """Return the href of a ``<th>`` link whose text is ``text``, if any."""
    match = re.search(rf'<th[^>]*>\s*<a href="([^"]+)"[^>]*>{text}</a>\s*</th>', html)
    return match.group(1) if match else None


# --- #82: access keys ----------------------------------------------------


def test_access_key_list_counts_presences(client, staff, make_presence):
    make_presence(identifier="a").save()
    make_presence(identifier="b").save()

    html = client.get(reverse("access_key_index")).content.decode()

    assert "Used by" not in html
    row = re.search(r"Test Key</a>\s*</td>\s*<td[^>]*>(\d+)</td>", html)
    assert row.group(1) == "2"


def test_access_key_list_headers_link_to_their_lists(client, staff, access_key):
    html = client.get(reverse("access_key_index")).content.decode()

    assert _header_link(html, "Presences") == reverse("index")
    assert _header_link(html, "Location") == reverse("location_index")


def test_menu_lists_map_last(client, staff):
    html = client.get(reverse("index")).content.decode()

    menu = re.search(r'<ul class="navbar-nav me-auto.*?</ul>', html, re.S).group(0)
    assert re.findall(r'class="nav-link"[^>]*>([^<]+)</a>', menu) == [
        "Locations",
        "Access keys",
        "Map",
    ]


# --- #83: presences ------------------------------------------------------


def test_presence_list_location_header_links_to_locations(client, staff, make_presence):
    make_presence().save()

    html = client.get(reverse("index")).content.decode()

    assert _header_link(html, "Location") == reverse("location_index")


# --- #84: locations ------------------------------------------------------


def test_location_list_presences_header_links_to_presences(client, staff, location):
    html = client.get(reverse("location_index")).content.decode()

    assert _header_link(html, "Presences") == reverse("index")
