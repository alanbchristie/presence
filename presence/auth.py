import hmac

from .models import AccessKey

API_KEY_HEADER = "X-API-Key"


def request_has_valid_key(request, access_key: AccessKey) -> bool:
    """Return True when the request carries the access key's secret value.

    The caller's ``X-API-Key`` header is compared with the linked key's value
    using ``hmac.compare_digest`` (constant-time). A missing or blank header
    is always rejected — there is no longer an "open" mode.
    """
    provided = request.headers.get(API_KEY_HEADER, "")
    if not provided:
        return False
    return hmac.compare_digest(access_key.value, provided)
