from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from django.db.models import ProtectedError

from .auth import request_has_valid_key
from .forms import AccessKeyForm, PresenceForm
from .models import AccessKey, Presence


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
    presence = get_object_or_404(Presence, identifier=identifier)
    if not request_has_valid_key(request, presence.access_key):
        return JsonResponse({"error": "forbidden"}, status=403)
    return JsonResponse(_serialize(presence))


@login_required
@require_GET
def index(request):
    presences = Presence.objects.order_by("name")
    return render(request, "presence/index.html", {"presences": presences})


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
    return render(request, "presence/access_key/index.html", {"keys": keys})


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
