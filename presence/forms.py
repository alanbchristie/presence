from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.db import IntegrityError, transaction

from . import what3words
from .models import (
    DEFAULT_LOCATION_NAME,
    AccessKey,
    Location,
    Presence,
    format_lat_lon,
    normalize_window_edge,
    parse_lat_lon,
)


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

class PresenceForm(forms.ModelForm):
    """Create/edit form for a :class:`~presence.models.Presence`.

    Exposes only the user-configurable fields; the runner-managed state
    (`current_state`, `state_since`, `next_transition_at`) and the auto
    timestamps are left off. Cross-field validation (duration ordering,
    solar-edge city requirement, zero-length window) is inherited from
    ``Presence.clean()``, which the ModelForm runs during validation.

    Each window edge is one string (issue #59): ``HH:MM`` for a wall-clock
    time, ``±HH:MM`` for a solar offset. The clean methods below normalise
    what the user typed (e.g. ``7:30`` → ``07:30``) so the stored form is
    canonical.

    Every presence needs an access key. The user either selects an existing
    key or supplies ``new_access_key_name`` to have one created and linked on
    save (issue #26, requirement 5).
    """

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
            "window_open",
            "window_close",
            "min_on_duration",
            "max_on_duration",
            "min_off_duration",
            "max_off_duration",
        ]
        widgets = {
            "window_open": forms.TextInput(
                attrs={"placeholder": "HH:MM or ±HH:MM"}
            ),
            "window_close": forms.TextInput(
                attrs={"placeholder": "HH:MM or ±HH:MM"}
            ),
        }
        # The window and duration fields render inside "Window" / "On" /
        # "Off" panels, so their labels drop the repeated context.
        labels = {
            "window_open": "Open",
            "window_close": "Close",
            "min_on_duration": "Min",
            "max_on_duration": "Max",
            "min_off_duration": "Min",
            "max_off_duration": "Max",
        }

    # Render the inline-create field directly after the access-key select.
    field_order = [
        "identifier",
        "name",
        "enabled",
        "location",
        "access_key",
        "new_access_key_name",
        "window_open",
        "window_close",
        "min_on_duration",
        "max_on_duration",
        "min_off_duration",
        "max_off_duration",
    ]

    # These run only after the model field's validate_window_edge accepted
    # the value, so normalisation cannot fail here.
    def clean_window_open(self) -> str:
        return normalize_window_edge(self.cleaned_data["window_open"])

    def clean_window_close(self) -> str:
        return normalize_window_edge(self.cleaned_data["window_close"])

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
        # Opt the duration inputs into the log-scale minute sliders that
        # presence/js/duration_slider.js attaches on the form pages.
        for field_name in (
            "min_on_duration",
            "max_on_duration",
            "min_off_duration",
            "max_off_duration",
        ):
            self.fields[field_name].widget.attrs["data-duration-slider"] = ""
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
    """Create/edit a :class:`~presence.models.Location`.

    Carries the ``timezone`` and ``city`` that the location's presences use for
    their window times (issue #43), and the optional map ``position`` (issue
    #54) — entered as a lat,lon pair (decimal, degrees with N/S/E/W, or
    degrees/minutes/seconds) or a What3Words address, but always stored
    (and redisplayed) as the decimal pair. The view guards
    against renaming the protected ``Default`` location.
    """

    class Meta:
        model = Location
        fields = ["name", "timezone", "city", "position"]
        help_texts = {
            "position": (
                "Optional. A 'lat,lon' pair — decimals (e.g. "
                "51.520847,-0.195521), degrees with N/S/E/W (e.g. "
                "36.35702° N, 5.24036° W), or degrees/minutes/seconds "
                "(e.g. 36°21'25\"N, 5°14'25\"W) — or a What3Words "
                "address (///filled.count.soap). When set, the map "
                "places this location here instead of at its city."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap_classes(self.fields.values())

    def clean_position(self) -> str:
        """Normalise the position to the stored decimal "lat,lon" form.

        A What3Words address is converted through the W3W API here, at
        validation time, so the model only ever stores (and the rest of
        the app only ever sees) the decimal pair.
        """
        value = (self.cleaned_data.get("position") or "").strip()
        if not value:
            return ""
        if what3words.looks_like_what3words(value):
            try:
                latitude, longitude = what3words.convert_to_coordinates(value)
            except what3words.What3WordsError as error:
                raise forms.ValidationError(str(error))
            return format_lat_lon(latitude, longitude)
        try:
            latitude, longitude = parse_lat_lon(value)
        except ValueError:
            raise forms.ValidationError(
                "Enter a 'lat,lon' pair — decimals (e.g. "
                "51.520847,-0.195521), degrees with N/S/E/W (e.g. "
                "36.35702° N, 5.24036° W), or degrees/minutes/seconds "
                "(e.g. 36°21'25\"N, 5°14'25\"W) — or a What3Words "
                "address (///filled.count.soap)."
            )
        return format_lat_lon(latitude, longitude)


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
