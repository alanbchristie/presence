from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .auth import require_api_key
from .models import Presence


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


@require_api_key
@require_GET
def presence_detail(request, identifier: str):
    presence = get_object_or_404(Presence, identifier=identifier)
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
@require_POST
def delete(request, identifier: str):
    presence = get_object_or_404(Presence, identifier=identifier)
    presence.delete()
    return redirect("index")
