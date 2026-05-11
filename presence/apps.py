from django.apps import AppConfig


class PresenceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "presence"

    def ready(self) -> None:
        from . import runner
        runner.start()
