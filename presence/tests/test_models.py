"""Unit tests for the Presence model.

Coverage areas:
  (a) the three field validator functions,
  (b) ``clean()`` — every branch of the absolute/solar validation matrix,
  (c) ``full_clean()`` — MinValueValidator + the unique identifier (DB),
  (d) window math with absolute times (wrap, boundaries, day-1 lookback),
  (e) timezone correctness across a DST boundary,
  (f) solar windows (wiring verified against astral, not hard-coded astronomy).

Only the two DB-hitting tests are marked ``django_db``; everything else
operates on unsaved instances and is pure.
"""
from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from astral.geocoder import database, lookup
from astral.sun import sun
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError, transaction

from presence.models import (
    Presence,
    validate_astral_city,
    validate_dns_label,
    validate_iana_timezone,
)

UTC = ZoneInfo("UTC")
LONDON = ZoneInfo("Europe/London")

DNS_MESSAGE = (
    "Identifier must be an RFC 1123 DNS label: 1-63 lowercase "
    "letters/digits, with optional internal hyphens (not at the "
    "start or end)."
)


# --- (a) validator functions ---------------------------------------------


@pytest.mark.parametrize("value", ["a", "a1", "lamp", "my-lamp-1", "a" * 63])
def test_validate_dns_label_accepts_valid(value):
    assert validate_dns_label(value) is None


@pytest.mark.parametrize(
    "value",
    ["", "-lamp", "lamp-", "Lamp", "la_mp", "la mp", "a" * 64, "lámp"],
)
def test_validate_dns_label_rejects_invalid(value):
    with pytest.raises(ValidationError) as exc:
        validate_dns_label(value)
    assert exc.value.messages == [DNS_MESSAGE]


@pytest.mark.parametrize("value", ["UTC", "Europe/London", "America/New_York"])
def test_validate_iana_timezone_accepts_valid(value):
    assert validate_iana_timezone(value) is None


@pytest.mark.parametrize("value", ["Mars/Phobos", "Definitely/NotAZone"])
def test_validate_iana_timezone_rejects_invalid(value):
    with pytest.raises(ValidationError) as exc:
        validate_iana_timezone(value)
    assert exc.value.messages == [f"{value!r} is not a valid IANA timezone name."]


def test_validate_astral_city_allows_empty():
    # Early-return branch: empty city is valid (absolute mode).
    assert validate_astral_city("") is None


def test_validate_astral_city_accepts_known_city():
    # Guard: if astral's DB drifts and drops London this fails loudly here.
    assert lookup("London", database()) is not None
    assert validate_astral_city("London") is None


def test_validate_astral_city_rejects_unknown_city():
    with pytest.raises(ValidationError) as exc:
        validate_astral_city("Nowhereville")
    assert exc.value.messages == [
        "'Nowhereville' is not in astral's built-in city database. "
        "See https://astral.readthedocs.io for the list."
    ]


# --- (b) clean() ----------------------------------------------------------


def test_clean_valid_baseline_passes(make_presence):
    make_presence().clean()  # must not raise


def test_clean_max_on_less_than_min_on(make_presence):
    p = make_presence(
        min_on_duration=timedelta(hours=2), max_on_duration=timedelta(hours=1)
    )
    with pytest.raises(ValidationError) as exc:
        p.clean()
    assert exc.value.message_dict["max_on_duration"] == ["Must be >= min_on_duration."]


def test_clean_max_on_equal_min_on_ok(make_presence):
    # Boundary: the check is `<`, not `<=`.
    make_presence(
        min_on_duration=timedelta(hours=1), max_on_duration=timedelta(hours=1)
    ).clean()


def test_clean_max_off_less_than_min_off(make_presence):
    p = make_presence(
        min_off_duration=timedelta(hours=2), max_off_duration=timedelta(hours=1)
    )
    with pytest.raises(ValidationError) as exc:
        p.clean()
    assert exc.value.message_dict["max_off_duration"] == ["Must be >= min_off_duration."]


def test_clean_absolute_requires_earliest_on(make_presence):
    p = make_presence(earliest_on=None)
    with pytest.raises(ValidationError) as exc:
        p.clean()
    assert exc.value.message_dict["earliest_on"] == [
        "Required unless 'earliest on relative to sunset' is checked."
    ]


def test_clean_absolute_requires_latest_off(make_presence):
    p = make_presence(latest_off=None)
    with pytest.raises(ValidationError) as exc:
        p.clean()
    assert exc.value.message_dict["latest_off"] == [
        "Required unless 'latest off relative to sunrise' is checked."
    ]


def test_clean_solar_open_requires_offset_not_earliest_on(make_presence):
    p = make_presence(
        earliest_on_relative_to_sunset=True,
        earliest_on=None,
        earliest_on_offset=None,
        city="London",
    )
    with pytest.raises(ValidationError) as exc:
        p.clean()
    md = exc.value.message_dict
    assert md["earliest_on_offset"] == [
        "Required when 'earliest on relative to sunset' is checked."
    ]
    # earliest_on is NOT required in the solar branch.
    assert "earliest_on" not in md


def test_clean_solar_close_requires_offset(make_presence):
    p = make_presence(
        latest_off_relative_to_sunrise=True,
        latest_off=None,
        latest_off_offset=None,
        city="London",
    )
    with pytest.raises(ValidationError) as exc:
        p.clean()
    assert exc.value.message_dict["latest_off_offset"] == [
        "Required when 'latest off relative to sunrise' is checked."
    ]


@pytest.mark.parametrize(
    "open_solar,close_solar",
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_clean_solar_matrix_city_requirement(make_presence, open_solar, close_solar):
    kwargs = {}
    if open_solar:
        kwargs.update(
            earliest_on_relative_to_sunset=True,
            earliest_on=None,
            earliest_on_offset=timedelta(minutes=-30),
        )
    if close_solar:
        kwargs.update(
            latest_off_relative_to_sunrise=True,
            latest_off=None,
            latest_off_offset=timedelta(minutes=15),
        )

    # city omitted (="") on the location:
    p = make_presence(city="", **kwargs)
    if open_solar or close_solar:
        with pytest.raises(ValidationError) as exc:
            p.clean()
        # The city now lives on the location, so the requirement surfaces as a
        # non-field error (issue #43).
        assert exc.value.message_dict[NON_FIELD_ERRORS] == [
            "The presence's location needs a city when either window "
            "edge is solar-relative."
        ]
    else:
        p.clean()  # pure absolute baseline, no city needed

    # city supplied -> passes regardless of mode.
    make_presence(city="London", **kwargs).clean()


def test_clean_city_error_isolated_when_offsets_present(make_presence):
    p = make_presence(
        earliest_on_relative_to_sunset=True,
        earliest_on=None,
        earliest_on_offset=timedelta(minutes=-30),
        city="",
    )
    with pytest.raises(ValidationError) as exc:
        p.clean()
    # Only the (non-field) missing-city error fires; the offset is present.
    assert list(exc.value.message_dict) == [NON_FIELD_ERRORS]


def test_clean_rejects_zero_length_absolute_window(make_presence):
    p = make_presence(earliest_on=time(20, 0), latest_off=time(20, 0))
    with pytest.raises(ValidationError) as exc:
        p.clean()
    assert exc.value.message_dict["latest_off"] == [
        "Must differ from earliest_on (a zero-length window is ambiguous)."
    ]


def test_clean_zero_length_check_suppressed_for_solar_edge(make_presence):
    # Same clock value on both edges, but the open edge is solar: the
    # zero-length guard must not fire (it is absolute-vs-absolute only).
    p = make_presence(
        earliest_on_relative_to_sunset=True,
        earliest_on=time(20, 0),
        earliest_on_offset=timedelta(minutes=-30),
        latest_off=time(20, 0),
        city="London",
    )
    p.clean()  # must not raise


def test_clean_accumulates_multiple_errors(make_presence):
    p = make_presence(
        min_on_duration=timedelta(hours=2),
        max_on_duration=timedelta(hours=1),
        earliest_on=None,
        latest_off=None,
    )
    with pytest.raises(ValidationError) as exc:
        p.clean()
    assert set(exc.value.message_dict) == {
        "max_on_duration",
        "earliest_on",
        "latest_off",
    }


# --- (c) full_clean() -----------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["min_on_duration", "max_on_duration", "min_off_duration", "max_off_duration"],
)
def test_full_clean_rejects_sub_minute_duration(make_presence, field):
    p = make_presence(**{field: timedelta(seconds=59)})
    with pytest.raises(ValidationError) as exc:
        p.full_clean(validate_unique=False)
    # Assert the offending key (not Django's localized message string).
    assert field in exc.value.message_dict


def test_full_clean_accepts_exactly_one_minute(make_presence):
    # MinValueValidator is inclusive.
    make_presence(
        min_on_duration=timedelta(minutes=1),
        max_on_duration=timedelta(minutes=1),
        min_off_duration=timedelta(minutes=1),
        max_off_duration=timedelta(minutes=1),
    ).full_clean(validate_unique=False)


def test_full_clean_runs_dns_label_validator(make_presence):
    p = make_presence(identifier="Bad_ID")
    with pytest.raises(ValidationError) as exc:
        p.full_clean(validate_unique=False)
    assert exc.value.message_dict["identifier"] == [DNS_MESSAGE]


@pytest.mark.django_db
def test_duplicate_identifier_rejected_by_full_clean(make_presence):
    make_presence(identifier="lamp").save()
    dup = make_presence(identifier="lamp", name="Other")
    with pytest.raises(ValidationError) as exc:
        dup.full_clean()
    assert "identifier" in exc.value.message_dict


@pytest.mark.django_db
def test_duplicate_identifier_rejected_at_db_level(make_presence):
    make_presence(identifier="lamp").save()
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make_presence(identifier="lamp", name="Other").save()


def test_str_returns_name(make_presence):
    assert str(make_presence(name="Hallway")) == "Hallway"


# --- (d) window math (absolute, UTC) -------------------------------------


def test_window_for_date_same_day(make_presence):
    p = make_presence(earliest_on=time(20, 0), latest_off=time(23, 0))
    open_dt, close_dt = p._window_for_date(date(2026, 1, 15))
    assert open_dt == datetime(2026, 1, 15, 20, 0, tzinfo=UTC)
    assert close_dt == datetime(2026, 1, 15, 23, 0, tzinfo=UTC)
    assert close_dt > open_dt


def test_window_for_date_wraps_past_midnight(make_presence):
    p = make_presence(earliest_on=time(22, 0), latest_off=time(6, 0))
    open_dt, close_dt = p._window_for_date(date(2026, 1, 15))
    assert open_dt == datetime(2026, 1, 15, 22, 0, tzinfo=UTC)
    assert close_dt == datetime(2026, 1, 16, 6, 0, tzinfo=UTC)


def test_window_for_date_equal_edges_roll_forward(make_presence):
    # _window_for_date is independent of clean(); equal edges -> close rolls.
    p = make_presence(earliest_on=time(8, 0), latest_off=time(8, 0))
    open_dt, close_dt = p._window_for_date(date(2026, 1, 15))
    assert close_dt == open_dt + timedelta(days=1)
    assert close_dt > open_dt


@pytest.mark.parametrize(
    "now,expected",
    [
        (datetime(2026, 1, 15, 20, 0, tzinfo=UTC), True),   # == open (inclusive)
        (datetime(2026, 1, 15, 22, 59, 59, tzinfo=UTC), True),
        (datetime(2026, 1, 15, 23, 0, tzinfo=UTC), False),  # == close (exclusive)
        (datetime(2026, 1, 15, 19, 59, 59, tzinfo=UTC), False),
    ],
)
def test_is_in_window_boundaries(make_presence, now, expected):
    p = make_presence(earliest_on=time(20, 0), latest_off=time(23, 0))
    assert p.is_in_window(now) is expected


def test_is_in_window_uses_previous_day_window(make_presence):
    # Wrap window 22:00->06:00; 02:00 falls inside the *previous* day's window.
    p = make_presence(earliest_on=time(22, 0), latest_off=time(6, 0))
    assert p.is_in_window(datetime(2026, 1, 15, 2, 0, tzinfo=UTC)) is True


def test_is_in_window_outside_all_windows(make_presence):
    p = make_presence(earliest_on=time(22, 0), latest_off=time(6, 0))
    assert p.is_in_window(datetime(2026, 1, 15, 12, 0, tzinfo=UTC)) is False


def test_next_window_open_returns_today(make_presence):
    p = make_presence(earliest_on=time(20, 0), latest_off=time(23, 0))
    now = datetime(2026, 1, 15, 19, 0, tzinfo=UTC)
    assert p.next_window_open(now) == datetime(2026, 1, 15, 20, 0, tzinfo=UTC)


def test_next_window_open_returns_tomorrow(make_presence):
    p = make_presence(earliest_on=time(20, 0), latest_off=time(23, 0))
    now = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
    assert p.next_window_open(now) == datetime(2026, 1, 16, 20, 0, tzinfo=UTC)


def test_window_close_after_inside_window(make_presence):
    p = make_presence(earliest_on=time(20, 0), latest_off=time(23, 0))
    now = datetime(2026, 1, 15, 21, 0, tzinfo=UTC)
    assert p.window_close_after(now) == datetime(2026, 1, 15, 23, 0, tzinfo=UTC)


def test_window_close_after_returns_tomorrow(make_presence):
    p = make_presence(earliest_on=time(20, 0), latest_off=time(23, 0))
    now = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
    assert p.window_close_after(now) == datetime(2026, 1, 16, 23, 0, tzinfo=UTC)


def test_window_close_after_wrap_case(make_presence):
    p = make_presence(earliest_on=time(22, 0), latest_off=time(6, 0))
    now = datetime(2026, 1, 15, 2, 0, tzinfo=UTC)
    assert p.window_close_after(now) == datetime(2026, 1, 15, 6, 0, tzinfo=UTC)


def test_next_window_open_raises_when_no_future_open(make_presence):
    # Contrived: force every candidate open into the past. This RuntimeError
    # is effectively unreachable for normal absolute config (see plan).
    p = make_presence()
    past = datetime(2000, 1, 1, tzinfo=UTC)
    with patch.object(p, "_window_open_for_date", return_value=past):
        with pytest.raises(RuntimeError, match="could not determine next window open"):
            p.next_window_open(datetime(2026, 1, 15, 12, 0, tzinfo=UTC))


def test_window_close_after_raises_when_no_future_close(make_presence):
    p = make_presence()
    past = datetime(2000, 1, 1, tzinfo=UTC)
    with patch.object(p, "_window_close_for_date", return_value=past):
        with pytest.raises(RuntimeError, match="could not determine window close"):
            p.window_close_after(datetime(2026, 1, 15, 12, 0, tzinfo=UTC))


# --- (e) timezone correctness (DST guard) --------------------------------


def test_window_open_localized_in_winter(make_presence):
    # January: Europe/London == UTC. 20:00 London == 20:00Z.
    p = make_presence(timezone="Europe/London", earliest_on=time(20, 0))
    open_dt = p._window_open_for_date(date(2026, 1, 15))
    assert open_dt.tzinfo == LONDON
    assert open_dt.astimezone(UTC) == datetime(2026, 1, 15, 20, 0, tzinfo=UTC)
    assert p.is_in_window(datetime(2026, 1, 15, 20, 0, tzinfo=UTC)) is True


def test_window_open_localized_in_summer_bst(make_presence):
    # July: Europe/London == UTC+1 (BST). 20:00 London == 19:00Z.
    p = make_presence(
        timezone="Europe/London", earliest_on=time(20, 0), latest_off=time(23, 0)
    )
    open_dt = p._window_open_for_date(date(2026, 7, 15))
    assert open_dt.tzinfo == LONDON
    assert open_dt.astimezone(UTC) == datetime(2026, 7, 15, 19, 0, tzinfo=UTC)
    assert p.is_in_window(datetime(2026, 7, 15, 19, 30, tzinfo=UTC)) is True
    assert p.is_in_window(datetime(2026, 7, 15, 18, 30, tzinfo=UTC)) is False


# --- (f) solar windows (verify wiring, not astronomy) --------------------


SOLSTICE = date(2026, 6, 21)


def _expected_sun(on_date):
    location = lookup("London", database())
    return sun(location.observer, date=on_date, tzinfo=LONDON)


def test_solar_open_is_sunset_plus_offset(make_presence):
    offset = timedelta(minutes=-30)
    p = make_presence(
        timezone="Europe/London",
        city="London",
        earliest_on_relative_to_sunset=True,
        earliest_on=None,
        earliest_on_offset=offset,
    )
    assert p._window_open_for_date(SOLSTICE) == _expected_sun(SOLSTICE)["sunset"] + offset


def test_solar_close_is_sunrise_plus_offset(make_presence):
    offset = timedelta(minutes=15)
    p = make_presence(
        timezone="Europe/London",
        city="London",
        latest_off_relative_to_sunrise=True,
        latest_off=None,
        latest_off_offset=offset,
    )
    assert p._window_close_for_date(SOLSTICE) == _expected_sun(SOLSTICE)["sunrise"] + offset


def test_mixed_mode_open_solar_close_absolute(make_presence):
    open_offset = timedelta(minutes=-30)
    p = make_presence(
        timezone="Europe/London",
        city="London",
        earliest_on_relative_to_sunset=True,
        earliest_on=None,
        earliest_on_offset=open_offset,
        latest_off_relative_to_sunrise=False,
        latest_off=time(23, 0),
    )
    assert p._window_open_for_date(SOLSTICE) == _expected_sun(SOLSTICE)["sunset"] + open_offset
    assert p._window_close_for_date(SOLSTICE) == datetime(
        2026, 6, 21, 23, 0, tzinfo=LONDON
    )


def test_solar_window_for_date_wraps_to_next_day(make_presence):
    # On the solstice, sunrise+15m is well before sunset-30m, so the close
    # rolls to the next day's sunrise.
    open_offset = timedelta(minutes=-30)
    close_offset = timedelta(minutes=15)
    p = make_presence(
        timezone="Europe/London",
        city="London",
        earliest_on_relative_to_sunset=True,
        earliest_on=None,
        earliest_on_offset=open_offset,
        latest_off_relative_to_sunrise=True,
        latest_off=None,
        latest_off_offset=close_offset,
    )
    open_dt, close_dt = p._window_for_date(SOLSTICE)
    assert open_dt == _expected_sun(SOLSTICE)["sunset"] + open_offset
    assert close_dt == _expected_sun(SOLSTICE + timedelta(days=1))["sunrise"] + close_offset
    assert close_dt > open_dt
