import re
from datetime import timedelta

from django import forms
from django.contrib.auth.forms import AuthenticationForm

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
