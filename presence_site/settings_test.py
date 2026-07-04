"""Test settings — the production settings with a throwaway secret key.

The production settings fail closed: with DEBUG off (the secure default) and
no ``DJANGO_SECRET_KEY`` they raise ``ImproperlyConfigured``. The test suite
runs with DEBUG off, so inject a disposable key here *before* importing the
real settings, keeping the suite runnable without weakening that behaviour or
requiring developers to export a key. pytest-django points at this module via
``DJANGO_SETTINGS_MODULE`` in ``pyproject.toml``.
"""
import os

os.environ.setdefault(
    "DJANGO_SECRET_KEY",
    "test-only-secret-key-not-used-in-production",
)

from presence_site.settings import *  # noqa: E402,F401,F403

# The suite always runs on in-memory SQLite, regardless of any DJANGO_DB_*
# vars in the surrounding environment (e.g. a shell configured for the
# docker-compose Postgres), so CI needs no database service.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# The Django test client issues plain-HTTP requests, so SECURE_SSL_REDIRECT
# (active because DEBUG is off) would turn every request into a 301. Relax just
# that one flag for the suite; the redirect logic itself is covered by
# test_settings.TestSecurityOverrides. The secure-cookie flags are left on —
# the in-memory test client is unaffected by the cookies' Secure attribute.
SECURE_SSL_REDIRECT = False  # noqa: F405
