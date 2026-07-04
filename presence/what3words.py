"""Convert a What3Words address to decimal coordinates (issue #54).

What3Words has no offline algorithm — conversion needs their REST API and
an account key, read from the ``W3W_API_KEY`` environment variable via
``settings.W3W_API_KEY``. Only the Location form calls this, and only
when the user types a three-word address, so an unconfigured key is a
form-time error rather than a boot-time one.

Uses stdlib urllib rather than adding an HTTP-client dependency for one
GET. The API key must never appear in error messages or logs.
"""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings

API_URL = "https://api.what3words.com/v3/convert-to-coordinates"
TIMEOUT_SECONDS = 10

# Three dot-separated words of letters only (no digits — "51.5.-0.1" must
# not match), with up to three leading slashes: ///filled.count.soap.
_W3W_RE = re.compile(r"^/{0,3}([^\W\d_]+)\.([^\W\d_]+)\.([^\W\d_]+)$")


class What3WordsError(Exception):
    """A conversion failure whose message is safe to show to the user."""


def looks_like_what3words(value: str) -> bool:
    return bool(_W3W_RE.match(value or ""))


def convert_to_coordinates(words: str) -> tuple[float, float]:
    """Resolve a three-word address to ``(latitude, longitude)``.

    Raises :class:`What3WordsError` (with a user-presentable message) when
    the key is unconfigured, the API rejects the address, the response is
    missing coordinates, or the lookup fails on the network.
    """
    api_key = settings.W3W_API_KEY
    if not api_key:
        raise What3WordsError(
            "What3Words lookup is not configured on this server "
            "(W3W_API_KEY is unset) — enter a decimal lat,lon instead."
        )
    query = urlencode({"words": words.lstrip("/"), "key": api_key})
    try:
        with urlopen(f"{API_URL}?{query}", timeout=TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except HTTPError as error:
        # 4xx responses carry a JSON body explaining the rejection.
        try:
            detail = json.load(error)["error"]["message"]
        except Exception:
            detail = f"HTTP {error.code}"
        raise What3WordsError(f"What3Words rejected {words!r}: {detail}")
    except (URLError, OSError, ValueError) as error:
        reason = error.reason if isinstance(error, URLError) else error
        raise What3WordsError(f"What3Words lookup failed: {reason}")
    coordinates = payload.get("coordinates") or {}
    if "lat" not in coordinates or "lng" not in coordinates:
        raise What3WordsError(
            f"What3Words returned no coordinates for {words!r}."
        )
    return float(coordinates["lat"]), float(coordinates["lng"])
