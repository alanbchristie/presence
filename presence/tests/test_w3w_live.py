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
import time
from pathlib import Path

import pytest

from presence import what3words
from presence.forms import LocationForm
from presence.models import parse_lat_lon

#: Minimum spacing between live API calls, so a burst of tests cannot
#: trip a per-second rate limit on the account's plan. (No spacing can
#: help with a spent monthly quota — those refusals skip, see _convert.)
CALL_SPACING_SECONDS = 1.0

_last_call_at = 0.0


def _spaced_call(function, *args):
    global _last_call_at
    wait = _last_call_at + CALL_SPACING_SECONDS - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        return function(*args)
    finally:
        _last_call_at = time.monotonic()


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


def _convert(words: str) -> tuple[float, float]:
    """Call the real converter, skipping on account-side refusals.

    A key can authenticate yet still be refused — monthly quota spent, or
    a plan without convert-to-coordinates access. That is a property of
    the account, not of this code, so it must not fail the build; skip
    with the API's explanation instead. Anything else propagates.
    """
    try:
        return _spaced_call(what3words.convert_to_coordinates, words)
    except what3words.What3WordsError as error:
        message = str(error)
        if "Quota" in message or "plan" in message:
            pytest.skip(f"W3W account cannot convert: {message}")
        raise


def test_convert_documented_example():
    latitude, longitude = _convert("///filled.count.soap")

    assert latitude == pytest.approx(51.520847, abs=1e-4)
    assert longitude == pytest.approx(-0.195521, abs=1e-4)


def test_convert_accepts_bare_words():
    latitude, longitude = _convert("filled.count.soap")

    assert latitude == pytest.approx(51.520847, abs=1e-4)


def test_convert_rejects_unknown_words():
    # Letter runs that are not in the What3Words wordlist: the API answers
    # 400 BadWords, surfaced as a user-presentable What3WordsError. Going
    # through _convert keeps quota/plan refusals a skip here too.
    with pytest.raises(what3words.What3WordsError):
        _convert("///aaaaaaaa.bbbbbbbb.cccccccc")


def test_form_stores_the_decimal_pair_for_a_w3w_address():
    _convert("///filled.count.soap")  # skip early on quota/plan refusals

    form = LocationForm(
        data={
            "name": "W3W Live",
            "timezone": "UTC",
            "city": "",
            "position": "///filled.count.soap",
        }
    )

    # Validation converts through the API, so it gets the same spacing.
    assert _spaced_call(form.is_valid), form.errors
    latitude, longitude = parse_lat_lon(form.cleaned_data["position"])
    assert latitude == pytest.approx(51.520847, abs=1e-4)
    assert longitude == pytest.approx(-0.195521, abs=1e-4)
