import hmac
import os
from functools import wraps

from django.http import JsonResponse

API_KEY_HEADER = "X-API-Key"
API_KEY_ENV_VAR = "PRESENCE_API_KEY"


def require_api_key(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        configured = os.environ.get(API_KEY_ENV_VAR, "")
        if not configured:
            return view(request, *args, **kwargs)
        provided = request.headers.get(API_KEY_HEADER, "")
        if not provided or not hmac.compare_digest(configured, provided):
            return JsonResponse({"error": "forbidden"}, status=403)
        return view(request, *args, **kwargs)

    return wrapper
