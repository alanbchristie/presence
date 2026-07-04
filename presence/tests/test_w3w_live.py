"""Live What3Words API tests (issue #54).

Unlike ``test_position.py`` (which mocks the HTTP layer), these call the
real convert-to-coordinates endpoint, so they need an account key. It is
taken from the ``W3W_API_KEY`` environment variable (CI exposes the
repository secret of the same name) or, failing that, from a ``.env``
file at the repository root (where local deployments keep it for docker
compose). With neither present — e.g. a fork's pull request, where
secrets are unavailable — the whole module skips rather than fails.

The three-word address exercised is What3Words' own documented example
(``///filled.count.soap``, their London office), so its coordinates are
stable.
"""
import os
from pathlib import Path

import pytest

from presence import what3words
from presence.forms import LocationForm
from presence.models import parse_lat_lon


def _key_from_dotenv() -> str:
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "W3W_API_KEY":
            return value.strip()
    return ""


W3W_API_KEY = os.environ.get("W3W_API_KEY", "").strip() or _key_from_dotenv()

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        not W3W_API_KEY,
        reason="no W3W_API_KEY in the environment or the repo-root .env",
    ),
]


@pytest.fixture(autouse=True)
def _configure_key(settings):
    # settings.W3W_API_KEY only picks up the process environment; inject
    # the key here so a .env-sourced value works the same way.
    settings.W3W_API_KEY = W3W_API_KEY


def test_convert_documented_example():
    latitude, longitude = what3words.convert_to_coordinates(
        "///filled.count.soap"
    )

    assert latitude == pytest.approx(51.520847, abs=1e-4)
    assert longitude == pytest.approx(-0.195521, abs=1e-4)


def test_convert_accepts_bare_words():
    latitude, longitude = what3words.convert_to_coordinates(
        "filled.count.soap"
    )

    assert latitude == pytest.approx(51.520847, abs=1e-4)


def test_convert_rejects_unknown_words():
    # Letter runs that are not in the What3Words wordlist: the API answers
    # 400 BadWords, surfaced as a user-presentable What3WordsError.
    with pytest.raises(what3words.What3WordsError):
        what3words.convert_to_coordinates("///aaaaaaaa.bbbbbbbb.cccccccc")


def test_form_stores_the_decimal_pair_for_a_w3w_address():
    form = LocationForm(
        data={
            "name": "W3W Live",
            "timezone": "UTC",
            "city": "",
            "position": "///filled.count.soap",
        }
    )

    assert form.is_valid(), form.errors
    latitude, longitude = parse_lat_lon(form.cleaned_data["position"])
    assert latitude == pytest.approx(51.520847, abs=1e-4)
    assert longitude == pytest.approx(-0.195521, abs=1e-4)
