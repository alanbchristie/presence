"""Tests for the security-relevant settings helpers.

These cover the secure-by-default posture introduced for the security review:
- DEBUG is off unless explicitly enabled (#1),
- a missing SECRET_KEY fails closed in production, with a dev fallback only
  when DEBUG is on (#2),
- HTTPS cookie/redirect/HSTS hardening is applied whenever DEBUG is off (#5),
- the database is env-selected: Postgres when DJANGO_DB_HOST is present,
  SQLite otherwise, so non-docker local dev needs zero setup (#47).
"""
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from presence_site.settings import (
    DEV_SECRET_KEY,
    env_bool,
    resolve_databases,
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


class TestResolveDatabases:
    BASE_DIR = Path("/checkout")

    def test_sqlite_default_without_db_host(self):
        config = resolve_databases({}, base_dir=self.BASE_DIR)
        default = config["default"]
        assert default["ENGINE"] == "django.db.backends.sqlite3"
        assert default["NAME"] == self.BASE_DIR / "db.sqlite3"

    def test_sqlite_path_from_environment(self):
        config = resolve_databases(
            {"PRESENCE_DB_PATH": "/data/db.sqlite3"}, base_dir=self.BASE_DIR
        )
        assert config["default"]["NAME"] == "/data/db.sqlite3"

    def test_postgres_when_db_host_present(self):
        config = resolve_databases(
            {
                "DJANGO_DB_HOST": "db",
                "DJANGO_DB_NAME": "presence",
                "DJANGO_DB_USER": "presence-user",
                "DJANGO_DB_PASSWORD": "not-a-real-password",
                "DJANGO_DB_PORT": "5433",
            },
            base_dir=self.BASE_DIR,
        )
        default = config["default"]
        assert default["ENGINE"] == "django.db.backends.postgresql"
        assert default["NAME"] == "presence"
        assert default["USER"] == "presence-user"
        assert default["PASSWORD"] == "not-a-real-password"
        assert default["HOST"] == "db"
        assert default["PORT"] == "5433"

    def test_postgres_defaults_for_optional_vars(self):
        config = resolve_databases(
            {"DJANGO_DB_HOST": "db"}, base_dir=self.BASE_DIR
        )
        default = config["default"]
        assert default["ENGINE"] == "django.db.backends.postgresql"
        assert default["NAME"] == "presence"
        assert default["USER"] == "presence"
        assert default["PORT"] == "5432"

    def test_blank_db_host_falls_back_to_sqlite(self):
        # An empty value (e.g. DJANGO_DB_HOST= in an env file) must not
        # select a Postgres config with no host.
        config = resolve_databases(
            {"DJANGO_DB_HOST": "  "}, base_dir=self.BASE_DIR
        )
        assert config["default"]["ENGINE"] == "django.db.backends.sqlite3"


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
