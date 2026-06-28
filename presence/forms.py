import re
from datetime import timedelta

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.db import IntegrityError, transaction

from .models import DEFAULT_LOCATION_NAME, AccessKey, Location, Presence


def _apply_bootstrap_classes(fields) -> None:
    """Tag each widget with the Bootstrap class that matches its type."""
    for field in fields:
        widget = field.widget
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs["class"] = "form-check-input"
        elif isinstance(widget, forms.Select):
            widget.attrs["class"] = "form-select"
        else:
            widget.attrs["class"] = "form-control"

_HH_MM_RE = re.compile(r"^([+-]?)(\d{1,3}):(\d{2})$")
_HH_MM_SS_RE = re.compile(r"^([+-]?)(\d{1,3}):(\d{2}):(\d{2})$")


class SignedDurationFormField(forms.DurationField):
    """Form field that renders/parses signed durations as ±HH:MM[:SS].

    Django's default DurationField formatting leaks Python's
    ``timedelta(days=-1, seconds=82800)`` representation (``"-1 23:00:00"``)
    for negative values, and prefers MM:SS for short colon-separated inputs.
    This subclass standardises on HH:MM (with optional :SS).
    """

    def prepare_value(self, value):
        if isinstance(value, timedelta):
            total = int(value.total_seconds())
            sign = "-" if total < 0 else "+"
            absolute = abs(total)
            hours, remainder = divmod(absolute, 3600)
            minutes, seconds = divmod(remainder, 60)
            if seconds:
                return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
            return f"{sign}{hours:02d}:{minutes:02d}"
        return super().prepare_value(value)

    def to_python(self, value):
        if value in (None, ""):
            return None
        if isinstance(value, timedelta):
            return value
        s = str(value).strip()
        m = _HH_MM_SS_RE.match(s)
        if m:
            sign_str, h, mn, sec = m.groups()
            td = timedelta(hours=int(h), minutes=int(mn), seconds=int(sec))
            return -td if sign_str == "-" else td
        m = _HH_MM_RE.match(s)
        if m:
            sign_str, h, mn = m.groups()
            td = timedelta(hours=int(h), minutes=int(mn))
            return -td if sign_str == "-" else td
        return super().to_python(value)


class PresenceForm(forms.ModelForm):
    """Create/edit form for a :class:`~presence.models.Presence`.

    Exposes only the user-configurable fields; the runner-managed state
    (`current_state`, `state_since`, `next_transition_at`) and the auto
    timestamps are left off. Cross-field validation (duration ordering,
    absolute-vs-solar window edges, city requirement) is inherited from
    ``Presence.clean()``, which the ModelForm runs during validation.

    The signed solar offsets reuse :class:`SignedDurationFormField` so they
    render and parse as ±HH:MM, matching the API and admin.

    Every presence needs an access key. The user either selects an existing
    key or supplies ``new_access_key_name`` to have one created and linked on
    save (issue #26, requirement 5).
    """

    earliest_on_offset = SignedDurationFormField(required=False)
    latest_off_offset = SignedDurationFormField(required=False)
    # Optional inline creation: when filled, a new key with this name is
    # created and linked, instead of selecting an existing key.
    new_access_key_name = forms.CharField(
        required=False,
        label="…or create a new access key named",
        help_text="Leave blank to use the selected key above.",
    )

    class Meta:
        model = Presence
        fields = [
            "identifier",
            "name",
            "enabled",
            "location",
            "access_key",
            "timezone",
            "earliest_on",
            "earliest_on_relative_to_sunset",
            "earliest_on_offset",
            "latest_off",
            "latest_off_relative_to_sunrise",
            "latest_off_offset",
            "city",
            "min_on_duration",
            "max_on_duration",
            "min_off_duration",
            "max_off_duration",
        ]
        widgets = {
            "earliest_on": forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
            "latest_off": forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
        }

    # Render the inline-create field directly after the access-key select.
    field_order = [
        "identifier",
        "name",
        "enabled",
        "location",
        "access_key",
        "new_access_key_name",
        "timezone",
        "earliest_on",
        "earliest_on_relative_to_sunset",
        "earliest_on_offset",
        "latest_off",
        "latest_off_relative_to_sunrise",
        "latest_off_offset",
        "city",
        "min_on_duration",
        "max_on_duration",
        "min_off_duration",
        "max_off_duration",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # An existing key OR an inline-created one satisfies the requirement,
        # so the select itself is not unconditionally required; clean() below
        # enforces that exactly one path is taken.
        self.fields["access_key"].required = False
        # Every presence needs a location, but the Default location stands in
        # when none is chosen (issue #33). Pre-select it on new rows and let
        # clean() fall back to it, so the select itself is not required.
        self.fields["location"].required = False
        if self.instance.pk is None:
            self.fields["location"].initial = (
                Location.objects.filter(name=DEFAULT_LOCATION_NAME).first()
            )
        _apply_bootstrap_classes(self.fields.values())

    def clean(self):
        cleaned = super().clean()

        # Fall back to the Default location when none was chosen, then make the
        # FK available to the model instance for _post_clean's non-null check.
        location = cleaned.get("location")
        if location is None:
            location = Location.objects.filter(name=DEFAULT_LOCATION_NAME).first()
            cleaned["location"] = location
        self.instance.location = location

        access_key = cleaned.get("access_key")
        new_name = (cleaned.get("new_access_key_name") or "").strip()

        if access_key and new_name:
            self.add_error(
                "new_access_key_name",
                "Choose an existing key or name a new one, not both.",
            )
            return cleaned
        if not access_key and not new_name:
            self.add_error(
                "access_key",
                "Select an access key or create a new one.",
            )
            return cleaned

        if new_name:
            # Create the key now (in a transaction) so the instance carries a
            # valid FK through the model's own non-null validation. Surface a
            # duplicate name rather than letting it pass silently.
            try:
                with transaction.atomic():
                    access_key = AccessKey.objects.create(name=new_name)
            except IntegrityError:
                self.add_error(
                    "new_access_key_name",
                    f"An access key named “{new_name}” already exists.",
                )
                return cleaned
            cleaned["access_key"] = access_key

        # Make the FK available to the model instance for _post_clean's
        # non-null check (which runs after this method).
        self.instance.access_key = access_key
        return cleaned


class AccessKeyForm(forms.ModelForm):
    """Create/rename an :class:`~presence.models.AccessKey`.

    Only the human-readable ``name`` is user-editable; the secret ``value`` is
    auto-generated by the model and never entered or shown in this form.
    """

    class Meta:
        model = AccessKey
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_classes(self.fields.values())


class LocationForm(forms.ModelForm):
    """Create/rename a :class:`~presence.models.Location`.

    Only the human-readable ``name`` is user-editable; the view guards against
    renaming the protected ``Default`` location.
    """

    class Meta:
        model = Location
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_classes(self.fields.values())


class BootstrapAuthenticationForm(AuthenticationForm):
    """Login form whose widgets carry Bootstrap's ``form-control`` class.

    Used by the ``LoginView`` (wired in ``presence_site.urls``) so the login
    template can render fields with ``{{ form.username }}`` and get
    Bootstrap-styled inputs without per-field markup.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
