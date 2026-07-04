import re
import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from astral.geocoder import database, lookup
from astral.sun import sun
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MinValueValidator
from django.db import models

MIN_DURATION = timedelta(minutes=1)

_DNS_LABEL_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


def validate_dns_label(value: str) -> None:
    if not _DNS_LABEL_RE.match(value or ""):
        raise ValidationError(
            "Identifier must be an RFC 1123 DNS label: 1-63 lowercase "
            "letters/digits, with optional internal hyphens (not at the "
            "start or end)."
        )


def validate_iana_timezone(value: str) -> None:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError:
        raise ValidationError(f"{value!r} is not a valid IANA timezone name.")


def validate_astral_city(value: str) -> None:
    if not value:
        return
    try:
        lookup(value, database())
    except KeyError:
        raise ValidationError(
            f"{value!r} is not in astral's built-in city database. "
            "See https://astral.readthedocs.io for the list."
        )


#: One coordinate: a decimal number, an optional degree symbol, and an
#: optional hemisphere letter. A sign and a hemisphere letter both give
#: the direction, so combining them is rejected in ``_parse_coordinate``.
_COORDINATE_RE = re.compile(
    r"^(?P<degrees>[+-]?\d+(?:\.\d+)?)\s*°?\s*(?P<hemisphere>[A-Za-z])?$"
)


def _parse_coordinate(text: str, hemispheres: str) -> float:
    """Parse one latitude or longitude from its textual form.

    Accepts a signed decimal ("-5.24036"), optionally with a degree
    symbol, or an unsigned decimal with one of the ``hemispheres``
    letters supplying the sign ("5.24036° W"). Raises ValueError for
    anything else.
    """
    match = _COORDINATE_RE.match(text.strip())
    if match is None:
        raise ValueError(f"{text!r} is not a decimal coordinate")
    degrees = float(match["degrees"])
    hemisphere = (match["hemisphere"] or "").upper()
    if hemisphere:
        if hemisphere not in hemispheres:
            raise ValueError(
                f"{text!r} does not end with one of {'/'.join(hemispheres)}"
            )
        if match["degrees"][0] in "+-":
            raise ValueError(
                f"{text!r} has both a sign and a hemisphere letter"
            )
        if hemisphere in "SW":
            degrees = -degrees
    return degrees


def parse_lat_lon(value: str) -> tuple[float, float]:
    """Parse a comma-separated "lat,lon" pair into decimal degrees.

    Each half is either a signed decimal ("51.520847,-0.195521") or
    decimal degrees with a hemisphere letter ("36.35702° N, 5.24036° W");
    the degree symbol is optional and S/W negate. Whitespace around
    either number is tolerated. Raises ValueError when the string is not
    two comma-separated coordinates, a sign and hemisphere letter are
    combined, or either number is outside its valid range (±90 latitude,
    ±180 longitude).
    """
    parts = (value or "").split(",")
    if len(parts) != 2:
        raise ValueError(f"{value!r} is not a 'lat,lon' pair")
    latitude = _parse_coordinate(parts[0], "NS")
    longitude = _parse_coordinate(parts[1], "EW")
    if not -90.0 <= latitude <= 90.0:
        raise ValueError(f"latitude {latitude} is outside ±90")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError(f"longitude {longitude} is outside ±180")
    return latitude, longitude


def format_lat_lon(latitude: float, longitude: float) -> str:
    """Render coordinates as the canonical stored "lat,lon" string.

    Six decimal places (about 0.1 m) with trailing zeros trimmed, so
    round-tripping a value the user typed does not grow it.
    """

    def _number(number: float) -> str:
        return f"{number:.6f}".rstrip("0").rstrip(".")

    return f"{_number(latitude)},{_number(longitude)}"


def validate_lat_lon(value: str) -> None:
    if not value:
        return
    try:
        parse_lat_lon(value)
    except ValueError:
        raise ValidationError(
            f"{value!r} is not a 'lat,lon' pair — use decimals "
            "(e.g. 51.520847,-0.195521) or degrees with N/S/E/W "
            "(e.g. 36.35702° N, 5.24036° W)."
        )


def generate_access_key_value() -> str:
    """Return a fresh, URL-safe secret for an :class:`AccessKey`.

    Used as the field default so keys created via the form, admin, or shell
    get a strong value without the user supplying one.
    """
    return secrets.token_urlsafe(32)


#: Name of the location seeded by migration 0011. It is the initial value of
#: every new presence and is protected from deletion and renaming so it always
#: exists and stays findable. The protected row is identified by this name,
#: matching the ``Default`` convention already used for the seeded access key.
DEFAULT_LOCATION_NAME = "Default"


class Location(models.Model):
    """A named place that one or more presences belong to.

    A presence belongs to exactly one location; a location can hold many
    presences. An access key's location(s) are derived through the presences
    that use it, so :class:`Location` carries no direct link to access keys.
    """

    name = models.CharField(
        max_length=64,
        unique=True,
        help_text="Human-readable label for this location (e.g. 'Office').",
    )
    timezone = models.CharField(
        max_length=64,
        default="UTC",
        validators=[validate_iana_timezone],
        help_text=(
            "IANA timezone name (e.g. Europe/London) that the wall-clock "
            "window times of presences at this location are interpreted in."
        ),
    )
    city = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[validate_astral_city],
        help_text=(
            "Name of a city from astral's built-in database. Required when a "
            "presence at this location uses a solar-relative window edge."
        ),
    )
    position = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[validate_lat_lon],
        help_text=(
            "Optional 'lat,lon' pair, as decimals (e.g. "
            "51.520847,-0.195521) or degrees with N/S/E/W (e.g. "
            "36.35702° N, 5.24036° W). When set, the map places this "
            "location here instead of at its city. The edit form also "
            "accepts a What3Words address (///word.word.word); all forms "
            "are stored as the decimal pair."
        ),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this location was created. Stored and shown in UTC.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this location was last saved. Stored and shown in UTC.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def coordinates(self) -> tuple[float, float] | None:
        """The ``(latitude, longitude)`` this location plots at, or None.

        An explicit ``position`` wins (issue #54); otherwise the city is
        resolved through astral's built-in database (the same source the
        solar window computation uses). None when neither is set, or —
        defensively, since both fields are validated on save — when the
        stored value is unparseable / unknown to astral.
        """
        if self.position:
            try:
                return parse_lat_lon(self.position)
            except ValueError:
                return None
        if not self.city:
            return None
        try:
            city = lookup(self.city, database())
        except KeyError:
            return None
        return (city.latitude, city.longitude)

    @property
    def in_use(self) -> bool:
        """True when at least one presence belongs to this location."""
        return self.presences.exists()

    @property
    def is_default(self) -> bool:
        """True for the protected, migration-seeded ``Default`` location."""
        return self.name == DEFAULT_LOCATION_NAME


class AccessKey(models.Model):
    """A named secret that protects API access to one or more presences.

    Each presence links to an access key, and the API validates the caller's
    ``X-API-Key`` header against the linked key's :attr:`value`.
    """

    name = models.CharField(
        max_length=64,
        unique=True,
        help_text="Human-readable label for this key (e.g. 'Living room').",
    )
    value = models.CharField(
        max_length=64,
        unique=True,
        default=generate_access_key_value,
        help_text=(
            "The secret sent in the X-API-Key header. Auto-generated; treat "
            "it as a password and do not share it in clear text."
        ),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this key was created. Stored and shown in UTC.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this key was last saved. Stored and shown in UTC.",
    )
    last_generated_at = models.DateTimeField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "When this key's value was last regenerated. Null for keys that "
            "have never been regenerated. Stored and shown in UTC."
        ),
    )

    class Meta:
        verbose_name = "Access key"
        verbose_name_plural = "Access keys"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def in_use(self) -> bool:
        """True when at least one presence links to this key."""
        return self.presences.exists()


class Presence(models.Model):
    class State(models.TextChoices):
        ON = "on", "on"
        OFF = "off", "off"

    identifier = models.CharField(
        max_length=63,
        unique=True,
        validators=[validate_dns_label],
        help_text=(
            "URL-safe identifier used in the REST API path. Must be an "
            "RFC 1123 DNS label: 1-63 lowercase letters/digits with "
            "optional internal hyphens."
        ),
    )
    name = models.CharField(
        max_length=64,
        help_text=(
            "Human-readable label shown in the admin. Not required to be "
            "unique; the `identifier` field is the unique key."
        ),
    )
    enabled = models.BooleanField(
        default=True,
        help_text=(
            "Uncheck to pause this row without deleting it. The runner thread "
            "skips disabled rows and stops mutating their state."
        ),
    )
    access_key = models.ForeignKey(
        AccessKey,
        on_delete=models.PROTECT,
        related_name="presences",
        help_text=(
            "Access key whose value the API requires in the X-API-Key header "
            "to read this presence. A key cannot be deleted while in use."
        ),
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="presences",
        help_text=(
            "Location this presence belongs to. A location cannot be deleted "
            "while presences reference it."
        ),
    )

    min_on_duration = models.DurationField(
        validators=[MinValueValidator(MIN_DURATION)],
        help_text=(
            "Clock-style duration HH:MM:SS, e.g. 00:01:30 for 1 min 30 sec, "
            "01:00:00 for 1 hour. Minimum 1 minute."
        ),
    )
    max_on_duration = models.DurationField(
        validators=[MinValueValidator(MIN_DURATION)],
        help_text=(
            "Clock-style duration HH:MM:SS, e.g. 02:00:00 for 2 hours. "
            "Must be >= min_on_duration."
        ),
    )
    min_off_duration = models.DurationField(
        validators=[MinValueValidator(MIN_DURATION)],
        help_text=(
            "Clock-style duration HH:MM:SS, e.g. 00:05:00 for 5 minutes. "
            "Minimum 1 minute."
        ),
    )
    max_off_duration = models.DurationField(
        validators=[MinValueValidator(MIN_DURATION)],
        help_text=(
            "Clock-style duration HH:MM:SS, e.g. 00:45:00 for 45 minutes. "
            "Must be >= min_off_duration."
        ),
    )

    earliest_on = models.TimeField(
        null=True,
        blank=True,
        help_text="Wall-clock time of day when the window opens. "
                  "Leave blank when 'earliest on relative to sunset' is checked.",
    )
    latest_off = models.TimeField(
        null=True,
        blank=True,
        help_text="Wall-clock time of day when the window closes. "
                  "Leave blank when 'latest off relative to sunrise' is checked. "
                  "If <= earliest_on, the window wraps past midnight.",
    )
    earliest_on_relative_to_sunset = models.BooleanField(
        default=False,
        help_text="If set, the window opens at sunset + earliest_on_offset (offset may be negative).",
    )
    earliest_on_offset = models.DurationField(
        null=True,
        blank=True,
        help_text="Signed offset from sunset, e.g. -1:00:00 for one hour before sunset. "
                  "Required when 'earliest on relative to sunset' is checked.",
    )
    latest_off_relative_to_sunrise = models.BooleanField(
        default=False,
        help_text="If set, the window closes at sunrise + latest_off_offset (offset may be negative).",
    )
    latest_off_offset = models.DurationField(
        null=True,
        blank=True,
        help_text="Signed offset from sunrise, e.g. 2:00:00 for two hours after sunrise. "
                  "Required when 'latest off relative to sunrise' is checked.",
    )
    current_state = models.CharField(
        max_length=3,
        choices=State.choices,
        default=State.OFF,
        help_text=(
            "Live on/off state, maintained by the background runner. "
            "Read-only; manual edits will be overwritten on the next tick."
        ),
    )
    state_since = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the current state was entered. Stored in UTC; the admin "
            "renders it in the row's timezone."
        ),
    )
    next_transition_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Scheduled time of the next on/off flip. Stored in UTC; the "
            "admin renders it in the row's timezone."
        ),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this row was first saved. Stored and shown in UTC.",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this row was last saved. Stored and shown in UTC.",
    )

    class Meta:
        verbose_name_plural = "Presences"

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        errors: dict[str, str] = {}
        if (
            self.min_on_duration is not None
            and self.max_on_duration is not None
            and self.max_on_duration < self.min_on_duration
        ):
            errors["max_on_duration"] = "Must be >= min_on_duration."
        if (
            self.min_off_duration is not None
            and self.max_off_duration is not None
            and self.max_off_duration < self.min_off_duration
        ):
            errors["max_off_duration"] = "Must be >= min_off_duration."

        # earliest_on edge: exactly one of absolute or solar must be configured
        if self.earliest_on_relative_to_sunset:
            if self.earliest_on_offset is None:
                errors["earliest_on_offset"] = (
                    "Required when 'earliest on relative to sunset' is checked."
                )
        else:
            if self.earliest_on is None:
                errors["earliest_on"] = (
                    "Required unless 'earliest on relative to sunset' is checked."
                )

        # latest_off edge: same dichotomy
        if self.latest_off_relative_to_sunrise:
            if self.latest_off_offset is None:
                errors["latest_off_offset"] = (
                    "Required when 'latest off relative to sunrise' is checked."
                )
        else:
            if self.latest_off is None:
                errors["latest_off"] = (
                    "Required unless 'latest off relative to sunrise' is checked."
                )

        # If any solar edge, the presence's location must name a city (the city
        # moved to Location in issue #43). Reported as a non-field error since
        # the city is not edited on the presence form.
        if self.earliest_on_relative_to_sunset or self.latest_off_relative_to_sunrise:
            location = self.location if self.location_id else None
            if location is None or not location.city:
                errors[NON_FIELD_ERRORS] = (
                    "The presence's location needs a city when either window "
                    "edge is solar-relative."
                )

        # absolute-vs-absolute zero-length check (still meaningful when both edges absolute)
        if (
            not self.earliest_on_relative_to_sunset
            and not self.latest_off_relative_to_sunrise
            and self.earliest_on is not None
            and self.latest_off is not None
            and self.earliest_on == self.latest_off
        ):
            errors["latest_off"] = (
                "Must differ from earliest_on (a zero-length window is ambiguous)."
            )

        if errors:
            raise ValidationError(errors)

    # --- helpers ---------------------------------------------------------

    def _zone(self) -> ZoneInfo:
        # Timezone and city live on the presence's Location (issue #43).
        return ZoneInfo(self.location.timezone)

    def _solar(self, on_date: date) -> dict:
        city = lookup(self.location.city, database())
        return sun(city.observer, date=on_date, tzinfo=self._zone())

    def _window_open_for_date(self, on_date: date) -> datetime:
        zone = self._zone()
        if self.earliest_on_relative_to_sunset:
            return self._solar(on_date)["sunset"] + self.earliest_on_offset
        return datetime.combine(on_date, self.earliest_on, tzinfo=zone)

    def _window_close_for_date(self, on_date: date) -> datetime:
        zone = self._zone()
        if self.latest_off_relative_to_sunrise:
            return self._solar(on_date)["sunrise"] + self.latest_off_offset
        return datetime.combine(on_date, self.latest_off, tzinfo=zone)

    def _window_for_date(self, on_date: date) -> tuple[datetime, datetime]:
        """Return (open_dt, close_dt) for the window anchored on `on_date`.
        close_dt is always strictly after open_dt; if the same-date close would
        be at or before the open, the close rolls to the next day (wrap).
        """
        open_dt = self._window_open_for_date(on_date)
        close_dt = self._window_close_for_date(on_date)
        if close_dt <= open_dt:
            close_dt = self._window_close_for_date(on_date + timedelta(days=1))
        return open_dt, close_dt

    def is_in_window(self, now: datetime) -> bool:
        local = now.astimezone(self._zone())
        today = local.date()
        for d in (today - timedelta(days=1), today, today + timedelta(days=1)):
            open_dt, close_dt = self._window_for_date(d)
            if open_dt <= now < close_dt:
                return True
        return False

    def next_window_open(self, now: datetime) -> datetime:
        local = now.astimezone(self._zone())
        today = local.date()
        for d in (today, today + timedelta(days=1), today + timedelta(days=2)):
            open_dt = self._window_open_for_date(d)
            if open_dt > now:
                return open_dt
        raise RuntimeError("could not determine next window open")

    def window_close_after(self, now: datetime) -> datetime:
        local = now.astimezone(self._zone())
        today = local.date()
        for d in (today - timedelta(days=1), today, today + timedelta(days=1), today + timedelta(days=2)):
            close_dt = self._window_close_for_date(d)
            if close_dt > now:
                return close_dt
        raise RuntimeError("could not determine window close")
