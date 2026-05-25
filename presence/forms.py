import re
from datetime import timedelta

from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import Presence

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
    """

    earliest_on_offset = SignedDurationFormField(required=False)
    latest_off_offset = SignedDurationFormField(required=False)

    class Meta:
        model = Presence
        fields = [
            "identifier",
            "name",
            "enabled",
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = "form-check-input"
            else:
                widget.attrs["class"] = "form-control"


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
