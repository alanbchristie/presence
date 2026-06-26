"""Tests for the security-relevant settings helpers.

These cover the secure-by-default posture introduced for the security review:
- DEBUG is off unless explicitly enabled (#1),
- a missing SECRET_KEY fails closed in production, with a dev fallback only
  when DEBUG is on (#2),
- HTTPS cookie/redirect/HSTS hardening is applied whenever DEBUG is off (#5).
"""
import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from presence_site.settings import (
    DEV_SECRET_KEY,
    env_bool,
    resolve_debug,
    resolve_secret_key,
    security_overrides,
)


class TestEnvBool:
    def test_absent_returns_default(self):
        assert env_bool({}, "X", default=False) is False
        assert env_bool({}, "X", default=True) is True

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "Yes", "on", " on "])
    def test_truthy_values(self, raw):
        assert env_bool({"X": raw}, "X", default=False) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "nope"])
    def test_falsey_values(self, raw):
        assert env_bool({"X": raw}, "X", default=True) is False


class TestResolveDebug:
    def test_defaults_off(self):
        # #1: the insecure default of True is gone.
        assert resolve_debug({}) is False

    def test_explicit_on(self):
        assert resolve_debug({"DJANGO_DEBUG": "true"}) is True

    def test_explicit_off(self):
        assert resolve_debug({"DJANGO_DEBUG": "false"}) is False


class TestResolveSecretKey:
    def test_uses_env_value(self):
        assert resolve_secret_key({"DJANGO_SECRET_KEY": "abc"}, debug=False) == "abc"

    def test_env_value_wins_even_in_debug(self):
        assert resolve_secret_key({"DJANGO_SECRET_KEY": "abc"}, debug=True) == "abc"

    def test_dev_fallback_only_when_debug(self):
        assert resolve_secret_key({}, debug=True) == DEV_SECRET_KEY

    def test_raises_when_missing_and_not_debug(self):
        # #2: fail closed rather than silently using a public key.
        with pytest.raises(ImproperlyConfigured):
            resolve_secret_key({}, debug=False)

    def test_blank_is_treated_as_missing(self):
        with pytest.raises(ImproperlyConfigured):
            resolve_secret_key({"DJANGO_SECRET_KEY": "   "}, debug=False)


class TestSecurityOverrides:
    def test_production_hardens_cookies_and_transport(self):
        overrides = security_overrides(debug=False)
        assert overrides["SESSION_COOKIE_SECURE"] is True
        assert overrides["CSRF_COOKIE_SECURE"] is True
        assert overrides["SECURE_SSL_REDIRECT"] is True
        assert overrides["SECURE_HSTS_SECONDS"] > 0

    def test_debug_leaves_transport_relaxed(self):
        overrides = security_overrides(debug=True)
        assert overrides["SESSION_COOKIE_SECURE"] is False
        assert overrides["CSRF_COOKIE_SECURE"] is False
        assert overrides["SECURE_SSL_REDIRECT"] is False
        assert overrides["SECURE_HSTS_SECONDS"] == 0


class TestAppliedSettings:
    def test_secure_cookies_active_with_debug_off(self):
        # pytest-django runs with DEBUG off, so the production hardening is
        # live in the loaded settings (#5).
        assert settings.DEBUG is False
        assert settings.SESSION_COOKIE_SECURE is True
        assert settings.CSRF_COOKIE_SECURE is True
        assert settings.SECURE_HSTS_SECONDS > 0
        # settings_test relaxes only the SSL redirect so the test client (which
        # speaks plain HTTP) is not 301'd on every request.
        assert settings.SECURE_SSL_REDIRECT is False
