"""Unit tests for the Presence model.

Coverage areas:
  (a) the field validator functions and the window-edge parse/format helpers,
  (b) ``clean()`` — every branch of the absolute/solar validation matrix,
  (c) ``full_clean()`` — MinValueValidator + the unique identifier (DB),
  (d) window math with absolute times (wrap, boundaries, day-1 lookback),
  (e) timezone correctness across a DST boundary,
  (f) solar windows (wiring verified against astral, not hard-coded astronomy).

A window edge is a single string (issue #59): ``HH:MM`` is an absolute
wall-clock time; ``+HH:MM`` / ``-HH:MM`` is a signed offset from sunset
(``window_open``) or sunrise (``window_close``) — the sign is what selects
solar mode, so ``+00:00`` means exactly sunset/sunrise while ``00:00`` means
midnight.

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
    format_window_edge,
    normalize_window_edge,
    parse_window_edge,
    validate_astral_city,
    validate_dns_label,
    validate_iana_timezone,
    validate_window_edge,
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


# --- (a) window-edge parse / normalize / validate --------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("20:00", time(20, 0)),
        ("00:00", time(0, 0)),
        ("23:59", time(23, 59)),
        ("07:05", time(7, 5)),
        ("7:05", time(7, 5)),  # 1-digit hour tolerated on input
        (" 20:00 ", time(20, 0)),  # surrounding whitespace tolerated
    ],
)
def test_parse_window_edge_absolute(value, expected):
    assert parse_window_edge(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("+00:00", timedelta(0)),  # exactly sunset/sunrise
        ("-00:30", timedelta(minutes=-30)),
        ("+01:15", timedelta(hours=1, minutes=15)),
        ("-23:59", -timedelta(hours=23, minutes=59)),
        ("+2:15", timedelta(hours=2, minutes=15)),  # 1-digit hour tolerated
    ],
)
def test_parse_window_edge_solar(value, expected):
    assert parse_window_edge(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "sunset",
        "20",
        "20:5",  # minutes must be two digits
        "24:00",  # absolute hours are 0-23
        "20:60",
        "+24:00",  # offset hours are 0-23 too
        "20:00:00",  # no seconds component
        "++01:00",
        "1000:00",
        "20.00",
    ],
)
def test_parse_window_edge_rejects_invalid(value):
    with pytest.raises(ValueError):
        parse_window_edge(value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("7:30", "07:30"),
        ("+1:00", "+01:00"),
        ("-0:05", "-00:05"),
        ("20:00", "20:00"),
        (" 20:00 ", "20:00"),
        ("+00:00", "+00:00"),
    ],
)
def test_normalize_window_edge(value, expected):
    assert normalize_window_edge(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (time(7, 5), "07:05"),
        (time(20, 0), "20:00"),
        (timedelta(0), "+00:00"),
        (timedelta(minutes=-30), "-00:30"),
        (timedelta(hours=2, minutes=15), "+02:15"),
    ],
)
def test_format_window_edge(value, expected):
    assert format_window_edge(value) == expected


@pytest.mark.parametrize("value", ["20:00", "+00:00", "-1:30"])
def test_format_round_trips_parse(value):
    assert format_window_edge(parse_window_edge(value)) == normalize_window_edge(value)


def test_validate_window_edge_accepts_valid():
    assert validate_window_edge("20:00") is None
    assert validate_window_edge("-01:30") is None


@pytest.mark.parametrize("value", ["", "24:00", "sunset", "+24:00"])
def test_validate_window_edge_rejects_invalid(value):
    with pytest.raises(ValidationError) as exc:
        validate_window_edge(value)
    assert exc.value.messages == [
        f"{value!r} is not a window edge: use HH:MM for a wall-clock time "
        "or +HH:MM / -HH:MM for a solar offset."
    ]


# --- solar-mode properties --------------------------------------------------


def test_window_edge_is_solar_properties(make_presence):
    absolute = make_presence(window_open="20:00", window_close="23:00")
    assert absolute.window_open_is_solar is False
    assert absolute.window_close_is_solar is False
    solar = make_presence(window_open="-00:30", window_close="+00:15")
    assert solar.window_open_is_solar is True
    assert solar.window_close_is_solar is True


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


@pytest.mark.parametrize(
    "open_solar,close_solar",
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_clean_solar_matrix_city_requirement(make_presence, open_solar, close_solar):
    kwargs = {}
    if open_solar:
        kwargs.update(window_open="-00:30")
    if close_solar:
        kwargs.update(window_close="+00:15")

    # city omitted (="") on the location:
    p = make_presence(city="", **kwargs)
    if open_solar or close_solar:
        with pytest.raises(ValidationError) as exc:
            p.clean()
        # The city lives on the location, so the requirement surfaces as a
        # non-field error (issue #43).
        assert exc.value.message_dict[NON_FIELD_ERRORS] == [
            "The presence's location needs a city when either window "
            "edge is solar-relative."
        ]
    else:
        p.clean()  # pure absolute baseline, no city needed

    # city supplied -> passes regardless of mode.
    make_presence(city="London", **kwargs).clean()


def test_clean_rejects_zero_length_absolute_window(make_presence):
    p = make_presence(window_open="20:00", window_close="20:00")
    with pytest.raises(ValidationError) as exc:
        p.clean()
    assert exc.value.message_dict["window_close"] == [
        "Must differ from window_open (a zero-length window is ambiguous)."
    ]


def test_clean_zero_length_check_suppressed_for_solar_edge(make_presence):
    # The same digits on both edges, but the open edge is solar (+00:00 is
    # sunset, not midnight): the zero-length guard must not fire — it is
    # absolute-vs-absolute only.
    p = make_presence(window_open="+00:00", window_close="00:00", city="London")
    p.clean()  # must not raise


def test_clean_tolerates_invalid_edge_strings(make_presence):
    # Field-level validation (full_clean) owns the format check; clean() must
    # not crash on an unparseable edge, and must still report other errors.
    p = make_presence(
        window_open="sunset",
        min_on_duration=timedelta(hours=2),
        max_on_duration=timedelta(hours=1),
    )
    with pytest.raises(ValidationError) as exc:
        p.clean()
    assert set(exc.value.message_dict) == {"max_on_duration"}


def test_clean_accumulates_multiple_errors(make_presence):
    p = make_presence(
        min_on_duration=timedelta(hours=2),
        max_on_duration=timedelta(hours=1),
        window_open="-00:30",
        city="",
    )
    with pytest.raises(ValidationError) as exc:
        p.clean()
    assert set(exc.value.message_dict) == {
        "max_on_duration",
        NON_FIELD_ERRORS,
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


@pytest.mark.parametrize("field", ["window_open", "window_close"])
def test_full_clean_rejects_blank_window_edge(make_presence, field):
    p = make_presence(**{field: ""})
    with pytest.raises(ValidationError) as exc:
        p.full_clean(validate_unique=False)
    assert field in exc.value.message_dict


@pytest.mark.parametrize("field", ["window_open", "window_close"])
def test_full_clean_runs_window_edge_validator(make_presence, field):
    p = make_presence(**{field: "24:00"})
    with pytest.raises(ValidationError) as exc:
        p.full_clean(validate_unique=False)
    assert exc.value.message_dict[field] == [
        "'24:00' is not a window edge: use HH:MM for a wall-clock time "
        "or +HH:MM / -HH:MM for a solar offset."
    ]


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
    p = make_presence(window_open="20:00", window_close="23:00")
    open_dt, close_dt = p._window_for_date(date(2026, 1, 15))
    assert open_dt == datetime(2026, 1, 15, 20, 0, tzinfo=UTC)
    assert close_dt == datetime(2026, 1, 15, 23, 0, tzinfo=UTC)
    assert close_dt > open_dt


def test_window_for_date_wraps_past_midnight(make_presence):
    p = make_presence(window_open="22:00", window_close="06:00")
    open_dt, close_dt = p._window_for_date(date(2026, 1, 15))
    assert open_dt == datetime(2026, 1, 15, 22, 0, tzinfo=UTC)
    assert close_dt == datetime(2026, 1, 16, 6, 0, tzinfo=UTC)


def test_window_for_date_equal_edges_roll_forward(make_presence):
    # _window_for_date is independent of clean(); equal edges -> close rolls.
    p = make_presence(window_open="08:00", window_close="08:00")
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
    p = make_presence(window_open="20:00", window_close="23:00")
    assert p.is_in_window(now) is expected


def test_is_in_window_uses_previous_day_window(make_presence):
    # Wrap window 22:00->06:00; 02:00 falls inside the *previous* day's window.
    p = make_presence(window_open="22:00", window_close="06:00")
    assert p.is_in_window(datetime(2026, 1, 15, 2, 0, tzinfo=UTC)) is True


def test_is_in_window_outside_all_windows(make_presence):
    p = make_presence(window_open="22:00", window_close="06:00")
    assert p.is_in_window(datetime(2026, 1, 15, 12, 0, tzinfo=UTC)) is False


def test_next_window_open_returns_today(make_presence):
    p = make_presence(window_open="20:00", window_close="23:00")
    now = datetime(2026, 1, 15, 19, 0, tzinfo=UTC)
    assert p.next_window_open(now) == datetime(2026, 1, 15, 20, 0, tzinfo=UTC)


def test_next_window_open_returns_tomorrow(make_presence):
    p = make_presence(window_open="20:00", window_close="23:00")
    now = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
    assert p.next_window_open(now) == datetime(2026, 1, 16, 20, 0, tzinfo=UTC)


def test_window_close_after_inside_window(make_presence):
    p = make_presence(window_open="20:00", window_close="23:00")
    now = datetime(2026, 1, 15, 21, 0, tzinfo=UTC)
    assert p.window_close_after(now) == datetime(2026, 1, 15, 23, 0, tzinfo=UTC)


def test_window_close_after_returns_tomorrow(make_presence):
    p = make_presence(window_open="20:00", window_close="23:00")
    now = datetime(2026, 1, 15, 23, 30, tzinfo=UTC)
    assert p.window_close_after(now) == datetime(2026, 1, 16, 23, 0, tzinfo=UTC)


def test_window_close_after_wrap_case(make_presence):
    p = make_presence(window_open="22:00", window_close="06:00")
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
    p = make_presence(timezone="Europe/London", window_open="20:00")
    open_dt = p._window_open_for_date(date(2026, 1, 15))
    assert open_dt.tzinfo == LONDON
    assert open_dt.astimezone(UTC) == datetime(2026, 1, 15, 20, 0, tzinfo=UTC)
    assert p.is_in_window(datetime(2026, 1, 15, 20, 0, tzinfo=UTC)) is True


def test_window_open_localized_in_summer_bst(make_presence):
    # July: Europe/London == UTC+1 (BST). 20:00 London == 19:00Z.
    p = make_presence(
        timezone="Europe/London", window_open="20:00", window_close="23:00"
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
    p = make_presence(
        timezone="Europe/London",
        city="London",
        window_open="-00:30",
    )
    expected = _expected_sun(SOLSTICE)["sunset"] - timedelta(minutes=30)
    assert p._window_open_for_date(SOLSTICE) == expected


def test_solar_close_is_sunrise_plus_offset(make_presence):
    p = make_presence(
        timezone="Europe/London",
        city="London",
        window_close="+00:15",
    )
    expected = _expected_sun(SOLSTICE)["sunrise"] + timedelta(minutes=15)
    assert p._window_close_for_date(SOLSTICE) == expected


def test_mixed_mode_open_solar_close_absolute(make_presence):
    p = make_presence(
        timezone="Europe/London",
        city="London",
        window_open="-00:30",
        window_close="23:00",
    )
    expected_open = _expected_sun(SOLSTICE)["sunset"] - timedelta(minutes=30)
    assert p._window_open_for_date(SOLSTICE) == expected_open
    assert p._window_close_for_date(SOLSTICE) == datetime(
        2026, 6, 21, 23, 0, tzinfo=LONDON
    )


def test_solar_window_for_date_wraps_to_next_day(make_presence):
    # On the solstice, sunrise+15m is well before sunset-30m, so the close
    # rolls to the next day's sunrise.
    p = make_presence(
        timezone="Europe/London",
        city="London",
        window_open="-00:30",
        window_close="+00:15",
    )
    open_dt, close_dt = p._window_for_date(SOLSTICE)
    assert open_dt == _expected_sun(SOLSTICE)["sunset"] - timedelta(minutes=30)
    assert close_dt == _expected_sun(SOLSTICE + timedelta(days=1))["sunrise"] + timedelta(
        minutes=15
    )
    assert close_dt > open_dt
