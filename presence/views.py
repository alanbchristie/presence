from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.forms import model_to_dict
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from django.db.models import ProtectedError

from . import ratelimit
from .auth import request_has_valid_key
from .forms import (
    AccessKeyForm,
    BootstrapAuthenticationForm,
    LocationForm,
    PresenceForm,
)
from .models import (
    DEFAULT_LOCATION_NAME,
    AccessKey,
    Location,
    Presence,
    generate_access_key_value,
)

# Failed-attempt thresholds for the auth endpoints (fixed window, per client
# IP). The API keys are 256-bit so brute force is already infeasible; these
# bound abuse and noise. Login is the realistic target, so it is stricter.
API_FAIL_LIMIT = 20
API_FAIL_WINDOW = 300  # seconds
LOGIN_FAIL_LIMIT = 5
LOGIN_FAIL_WINDOW = 300  # seconds


def _seconds(dt: datetime | None, zone: ZoneInfo) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(zone).replace(microsecond=0).isoformat()


def _hhmm(td: timedelta) -> str:
    total_minutes = int(td.total_seconds() // 60)
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _hhmm_signed(td: timedelta) -> str:
    seconds = int(td.total_seconds())
    sign = "-" if seconds < 0 else "+"
    minutes = abs(seconds) // 60
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _serialize(p: Presence) -> dict:
    now = timezone.now()
    zone = ZoneInfo(p.timezone)
    return {
        "id": p.pk,
        "identifier": p.identifier,
        "name": p.name,
        "enabled": p.enabled,
        "access_key": p.access_key.name,
        "timezone": p.timezone,
        "min_on_duration": _hhmm(p.min_on_duration),
        "max_on_duration": _hhmm(p.max_on_duration),
        "min_off_duration": _hhmm(p.min_off_duration),
        "max_off_duration": _hhmm(p.max_off_duration),
        "earliest_on": p.earliest_on.isoformat(timespec="minutes") if p.earliest_on else None,
        "latest_off": p.latest_off.isoformat(timespec="minutes") if p.latest_off else None,
        "earliest_on_relative_to_sunset": p.earliest_on_relative_to_sunset,
        "earliest_on_offset": _hhmm_signed(p.earliest_on_offset) if p.earliest_on_offset is not None else None,
        "latest_off_relative_to_sunrise": p.latest_off_relative_to_sunrise,
        "latest_off_offset": _hhmm_signed(p.latest_off_offset) if p.latest_off_offset is not None else None,
        "city": p.city or None,
        "state": p.current_state,
        "state_since": _seconds(p.state_since, zone),
        "next_transition_at": _seconds(p.next_transition_at, zone),
        "in_window": p.is_in_window(now),
        "now": _seconds(now, zone),
    }


@require_GET
def presence_detail(request, identifier: str):
    ip = ratelimit.client_ip(request)
    if ratelimit.is_blocked("api", ip, limit=API_FAIL_LIMIT):
        return JsonResponse({"error": "too many requests"}, status=429)

    # Resolve without get_object_or_404 so an unknown identifier and a bad key
    # produce the same 403 — callers must not be able to tell which presences
    # exist (#6).
    presence = Presence.objects.filter(identifier=identifier).first()
    if presence is None or not request_has_valid_key(request, presence.access_key):
        ratelimit.record_failure("api", ip, window_seconds=API_FAIL_WINDOW)
        return JsonResponse({"error": "forbidden"}, status=403)

    ratelimit.clear("api", ip)
    return JsonResponse(_serialize(presence))


def _selected_location_id(request) -> int | None:
    """Return a valid ``?location=<pk>`` filter, or None to show everything.

    A missing, non-integer, or unknown id falls back to None (no filtering)
    rather than erroring or showing an empty list.
    """
    raw = request.GET.get("location")
    if not raw:
        return None
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        return None
    return pk if Location.objects.filter(pk=pk).exists() else None


@login_required
@require_GET
def index(request):
    presences = Presence.objects.order_by("name")
    selected = _selected_location_id(request)
    if selected is not None:
        presences = presences.filter(location_id=selected)
    return render(
        request,
        "presence/index.html",
        {
            "presences": presences,
            "locations": Location.objects.all(),
            "selected_location": selected,
        },
    )


@login_required
@require_GET
def detail(request, identifier: str):
    presence = get_object_or_404(Presence, identifier=identifier)
    return render(
        request,
        "presence/detail.html",
        {"presence": presence, "data": _serialize(presence)},
    )


@login_required
@require_http_methods(["GET", "POST"])
def add(request):
    if request.method == "POST":
        form = PresenceForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("index")
    else:
        form = PresenceForm()
    return render(request, "presence/add.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def edit(request, identifier: str):
    presence = get_object_or_404(Presence, identifier=identifier)
    if request.method == "POST":
        form = PresenceForm(request.POST, instance=presence)
        if form.is_valid():
            form.save()
            # The identifier may have changed; redirect to the saved value.
            return redirect("detail", identifier=form.instance.identifier)
    else:
        form = PresenceForm(instance=presence)
    return render(request, "presence/edit.html", {"form": form, "presence": presence})


@login_required
@require_http_methods(["GET", "POST"])
def duplicate(request, identifier: str):
    """Create a new presence pre-filled from an existing one (issue #40).

    GET renders the create form populated with the source's configuration so
    the user only has to give the copy a new identifier and name (and may pick
    a different location). The unique ``identifier`` is intentionally left
    blank. POST goes through the ordinary ``PresenceForm`` create path, so the
    source row is never touched.
    """
    source = get_object_or_404(Presence, identifier=identifier)
    if request.method == "POST":
        form = PresenceForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("detail", identifier=form.instance.identifier)
    else:
        initial = model_to_dict(source, fields=PresenceForm.Meta.fields)
        # The identifier is unique, so it must not carry over: force the user
        # to supply a fresh one for the copy.
        initial["identifier"] = ""
        form = PresenceForm(initial=initial)
    return render(
        request,
        "presence/duplicate.html",
        {"form": form, "source": source},
    )


@login_required
@require_POST
def delete(request, identifier: str):
    presence = get_object_or_404(Presence, identifier=identifier)
    presence.delete()
    return redirect("index")


# --- access key management ----------------------------------------------


@login_required
@require_GET
def access_key_index(request):
    keys = AccessKey.objects.order_by("name")
    selected = _selected_location_id(request)
    if selected is not None:
        # A key's location(s) are derived through the presences that use it, so
        # narrow to keys used by any presence at the selected location. distinct
        # collapses a key shared by several presences there into one row.
        keys = keys.filter(presences__location_id=selected).distinct()
    return render(
        request,
        "presence/access_key/index.html",
        {
            "keys": keys,
            "locations": Location.objects.all(),
            "selected_location": selected,
        },
    )


@login_required
@require_GET
def access_key_detail(request, pk: int):
    key = get_object_or_404(AccessKey, pk=pk)
    return render(request, "presence/access_key/detail.html", {"key": key})


@login_required
@require_http_methods(["GET", "POST"])
def access_key_add(request):
    if request.method == "POST":
        form = AccessKeyForm(request.POST)
        if form.is_valid():
            key = form.save()
            return redirect("access_key_detail", pk=key.pk)
    else:
        form = AccessKeyForm()
    return render(request, "presence/access_key/add.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def access_key_edit(request, pk: int):
    key = get_object_or_404(AccessKey, pk=pk)
    if request.method == "POST":
        form = AccessKeyForm(request.POST, instance=key)
        if form.is_valid():
            form.save()
            return redirect("access_key_detail", pk=key.pk)
    else:
        form = AccessKeyForm(instance=key)
    return render(request, "presence/access_key/edit.html", {"form": form, "key": key})


@login_required
@require_POST
def access_key_delete(request, pk: int):
    key = get_object_or_404(AccessKey, pk=pk)
    # Requirement #4: a key in use by any presence must not be deleted. The
    # PROTECT FK enforces this at the DB layer; catch it for a friendly path.
    if key.in_use:
        messages.error(
            request,
            f"Cannot delete “{key.name}”: it is still used by "
            f"{key.presences.count()} presence record(s).",
        )
        return redirect("access_key_detail", pk=key.pk)
    try:
        key.delete()
    except ProtectedError:
        messages.error(request, f"Cannot delete “{key.name}”: it is still in use.")
        return redirect("access_key_detail", pk=key.pk)
    return redirect("access_key_index")


@login_required
@require_POST
def access_key_regenerate(request, pk: int):
    key = get_object_or_404(AccessKey, pk=pk)
    # Rotate the secret. In-use keys can be regenerated (callers must then
    # update their X-API-Key); only deletion is PROTECT-guarded.
    key.value = generate_access_key_value()
    key.last_generated_at = timezone.now()
    key.save(update_fields=["value", "last_generated_at", "updated_at"])
    # Surface the new secret once to the owner. The detail page already shows
    # the value in plain text, and a session message is not application
    # logging, so this does not conflict with the "never log secrets" rule.
    messages.success(
        request,
        f"Regenerated “{key.name}”. New value: {key.value} — "
        f"update any callers now.",
    )
    return redirect("access_key_detail", pk=key.pk)


# --- location management -------------------------------------------------


@login_required
@require_GET
def location_index(request):
    locations = Location.objects.order_by("name")
    return render(request, "presence/location/index.html", {"locations": locations})


@login_required
@require_GET
def location_detail(request, pk: int):
    location = get_object_or_404(Location, pk=pk)
    # A location's access keys are derived through the presences at it.
    access_keys = (
        AccessKey.objects.filter(presences__location=location).distinct().order_by("name")
    )
    return render(
        request,
        "presence/location/detail.html",
        {"location": location, "access_keys": access_keys},
    )


@login_required
@require_http_methods(["GET", "POST"])
def location_add(request):
    if request.method == "POST":
        form = LocationForm(request.POST)
        if form.is_valid():
            location = form.save()
            return redirect("location_detail", pk=location.pk)
    else:
        form = LocationForm()
    return render(request, "presence/location/add.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def location_edit(request, pk: int):
    location = get_object_or_404(Location, pk=pk)
    if request.method == "POST":
        # The Default location must stay findable by name, so it is not
        # renameable (it is also not deletable).
        if location.is_default:
            messages.error(
                request,
                f"The “{DEFAULT_LOCATION_NAME}” location cannot be renamed.",
            )
            return redirect("location_detail", pk=location.pk)
        form = LocationForm(request.POST, instance=location)
        if form.is_valid():
            form.save()
            return redirect("location_detail", pk=location.pk)
    else:
        form = LocationForm(instance=location)
    return render(
        request,
        "presence/location/edit.html",
        {"form": form, "location": location},
    )


@login_required
@require_http_methods(["GET", "POST"])
def location_duplicate(request, pk: int):
    """Create a new location seeded from an existing one (issue #40).

    A location only carries a unique ``name``, so duplication just renders the
    create form with the name left blank for the user to supply a new one; the
    source row is never modified.
    """
    source = get_object_or_404(Location, pk=pk)
    if request.method == "POST":
        form = LocationForm(request.POST)
        if form.is_valid():
            location = form.save()
            return redirect("location_detail", pk=location.pk)
    else:
        # The name is unique; leave it blank for the user to supply a new one.
        form = LocationForm(initial={"name": ""})
    return render(
        request,
        "presence/location/duplicate.html",
        {"form": form, "source": source},
    )


@login_required
@require_POST
def location_delete(request, pk: int):
    location = get_object_or_404(Location, pk=pk)
    # The Default location is permanent (issue #33).
    if location.is_default:
        messages.error(
            request,
            f"The “{DEFAULT_LOCATION_NAME}” location cannot be deleted.",
        )
        return redirect("location_detail", pk=location.pk)
    # A location in use by any presence must not be deleted. The PROTECT FK
    # enforces this at the DB layer; catch it for a friendly path.
    if location.in_use:
        messages.error(
            request,
            f"Cannot delete “{location.name}”: it is still used by "
            f"{location.presences.count()} presence record(s).",
        )
        return redirect("location_detail", pk=location.pk)
    try:
        location.delete()
    except ProtectedError:
        messages.error(
            request, f"Cannot delete “{location.name}”: it is still in use."
        )
        return redirect("location_detail", pk=location.pk)
    return redirect("location_index")


# --- authentication ------------------------------------------------------


class ThrottledLoginView(LoginView):
    """Login view that rate-limits failed attempts per client IP (#7).

    Once a client exceeds ``LOGIN_FAIL_LIMIT`` failures within the window, every
    POST is refused with HTTP 429 — including one carrying the correct password
    — until the window lapses. A successful login clears the counter. GET (just
    rendering the form) is never throttled.
    """

    authentication_form = BootstrapAuthenticationForm

    def post(self, request, *args, **kwargs):
        ip = ratelimit.client_ip(request)
        if ratelimit.is_blocked("login", ip, limit=LOGIN_FAIL_LIMIT):
            context = self.get_context_data(form=self.get_form(), rate_limited=True)
            return self.render_to_response(context, status=429)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        ratelimit.clear("login", ratelimit.client_ip(self.request))
        return super().form_valid(form)

    def form_invalid(self, form):
        ratelimit.record_failure(
            "login",
            ratelimit.client_ip(self.request),
            window_seconds=LOGIN_FAIL_WINDOW,
        )
        return super().form_invalid(form)
