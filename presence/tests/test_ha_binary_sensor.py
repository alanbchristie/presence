"""Tests for the "View as HA Binary Sensor" page (issue #78).

The detail page links to a login-gated view that renders the Home Assistant
``binary_sensor`` YAML an operator can paste into ``configuration.yaml`` to
poll this presence's API endpoint with its access key.
"""
import pytest
import yaml
from django.urls import reverse

from .conftest import VALID_KWARGS

pytestmark = pytest.mark.django_db

IDENTIFIER = VALID_KWARGS["identifier"]


@pytest.fixture
def staff(client, django_user_model):
    user = django_user_model.objects.create_user(username="staff", password="pw")
    client.force_login(user)
    return user


def _sensor(response) -> dict:
    """The single REST binary sensor the rendered YAML declares."""
    document = yaml.safe_load(response.content.decode())
    [sensor] = document["binary_sensor"]
    return sensor


def test_detail_links_to_ha_binary_sensor_view(client, staff, make_presence):
    make_presence().save()

    body = client.get(reverse("detail", args=[IDENTIFIER])).content.decode()

    ha_url = reverse("detail_ha_binary_sensor", args=[IDENTIFIER])
    assert f'href="{ha_url}"' in body
    assert "View as HA Binary Sensor" in body
    # It sits below the "View as JSON" link.
    assert body.index("View as JSON") < body.index("View as HA Binary Sensor")


def test_ha_binary_sensor_redirects_anonymous_to_login(client, make_presence):
    make_presence().save()

    response = client.get(reverse("detail_ha_binary_sensor", args=[IDENTIFIER]))

    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_ha_binary_sensor_unknown_identifier_returns_404(client, staff):
    response = client.get(reverse("detail_ha_binary_sensor", args=["does-not-exist"]))

    assert response.status_code == 404


def test_ha_binary_sensor_is_served_as_plain_text(client, staff, make_presence):
    make_presence().save()

    response = client.get(reverse("detail_ha_binary_sensor", args=[IDENTIFIER]))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/plain; charset=utf-8"


def test_ha_binary_sensor_yaml_describes_a_rest_sensor(client, staff, make_presence):
    presence = make_presence()
    presence.save()

    response = client.get(reverse("detail_ha_binary_sensor", args=[IDENTIFIER]))

    sensor = _sensor(response)
    assert sensor["platform"] == "rest"
    assert sensor["name"] == f"Presence Simulator ({presence.name})"
    # The resource is the absolute URL of the key-protected API endpoint.
    assert sensor["resource"] == f"http://testserver/api/presence/{IDENTIFIER}/"
    # The header carries the presence's actual access key, not a placeholder.
    assert sensor["headers"] == {"X-API-Key": presence.access_key.value}
    # Home Assistant's templates survive verbatim (not eaten by Django's).
    assert sensor["value_template"] == "{{ value_json.state }}"
    assert sensor["icon"] == "{{ 'mdi:shield-home' }}"


def test_ha_binary_sensor_resource_follows_request_scheme_and_host(
    client, staff, make_presence, settings
):
    # Behind Caddy the scheme arrives via X-Forwarded-Proto and the host
    # (with any non-default port) via Host; the snippet must echo both.
    settings.ALLOWED_HOSTS = ["presence.example.org"]
    make_presence().save()

    response = client.get(
        reverse("detail_ha_binary_sensor", args=[IDENTIFIER]),
        secure=True,
        headers={"host": "presence.example.org:8443"},
    )

    assert _sensor(response)["resource"] == (
        f"https://presence.example.org:8443/api/presence/{IDENTIFIER}/"
    )


def test_ha_binary_sensor_quotes_awkward_names(client, staff, make_presence):
    # YAML-significant characters in the name must not break the document,
    # and HTML-significant ones must not be entity-escaped.
    make_presence(name='Office: "front" & #1').save()

    response = client.get(reverse("detail_ha_binary_sensor", args=[IDENTIFIER]))

    assert _sensor(response)["name"] == 'Presence Simulator (Office: "front" & #1)'
