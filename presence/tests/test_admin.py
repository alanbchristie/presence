"""Tests for the Django admin customisations.

The Presence changelist links each row's Location to that Location's own
admin change page (mirroring the web UI list screen).
"""

from django.urls import reverse


def test_presence_changelist_links_location_to_its_admin_change_page(
    admin_client, make_presence, location
):
    make_presence().save()

    body = admin_client.get(
        reverse("admin:presence_presence_changelist")
    ).content.decode()

    location_change_url = reverse(
        "admin:presence_location_change", args=[location.pk]
    )
    assert f'href="{location_change_url}"' in body
    assert location.name in body
