from zoneinfo import ZoneInfo

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import AccessKey, Location, Presence


@admin.register(AccessKey)
class AccessKeyAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "in_use", "created_at")
    list_display_links = ("id", "name")
    search_fields = ("name",)
    readonly_fields = ("value", "created_at", "updated_at", "last_generated_at")

    @admin.display(boolean=True, description="In use")
    def in_use(self, obj):
        return obj.in_use


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "timezone",
        "city",
        "position",
        "in_use",
        "created_at",
    )
    list_display_links = ("id", "name")
    search_fields = ("name", "timezone", "city")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(boolean=True, description="In use")
    def in_use(self, obj):
        return obj.in_use


@admin.register(Presence)
class PresenceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "identifier",
        "name",
        "enabled",
        "current_state",
        "state_since_local",
        "next_transition_at_local",
        "window_open",
        "window_close",
        "location_link",
    )
    list_display_links = ("id", "identifier", "name")
    list_filter = ("enabled", "current_state", "location")
    search_fields = ("identifier", "name", "id")
    prepopulated_fields = {"identifier": ("name",)}
    readonly_fields = (
        "id",
        "current_state",
        "state_since_local",
        "next_transition_at_local",
        "created_at_utc",
        "updated_at_utc",
    )
    fieldsets = (
        ("Identity", {"fields": ("id", "identifier", "name", "enabled", "location", "access_key")}),
        (
            "Durations",
            {
                "fields": (
                    "min_on_duration",
                    "max_on_duration",
                    "min_off_duration",
                    "max_off_duration",
                ),
            },
        ),
        (
            "Window",
            {
                "fields": (
                    "window_open",
                    "window_close",
                ),
            },
        ),
        (
            "Live state",
            {"fields": ("current_state", "state_since_local", "next_transition_at_local")},
        ),
        ("Audit", {"fields": ("created_at_utc", "updated_at_utc")}),
    )

    @admin.display(description="Location", ordering="location__name")
    def location_link(self, obj):
        url = reverse(
            "admin:presence_location_change", args=[obj.location.pk]
        )
        return format_html('<a href="{}">{}</a>', url, obj.location.name)

    @admin.display(description="State since (row tz)", ordering="state_since")
    def state_since_local(self, obj):
        return self._format_local(obj, obj.state_since)

    @admin.display(description="Next transition (row tz)", ordering="next_transition_at")
    def next_transition_at_local(self, obj):
        return self._format_local(obj, obj.next_transition_at)

    @admin.display(description="Created at (UTC)")
    def created_at_utc(self, obj):
        return self._format_utc(obj.created_at)

    @admin.display(description="Updated at (UTC)")
    def updated_at_utc(self, obj):
        return self._format_utc(obj.updated_at)

    @staticmethod
    def _format_local(obj, dt):
        if dt is None:
            return "—"
        try:
            zone = ZoneInfo(obj.location.timezone)
        except Exception:
            return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        local = dt.astimezone(zone)
        return local.strftime("%Y-%m-%d %H:%M:%S %Z")

    @staticmethod
    def _format_utc(dt):
        if dt is None:
            return "—"
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
