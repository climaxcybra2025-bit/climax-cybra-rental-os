from django.apps import AppConfig


class OperationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.operations"

    def ready(self):
        from . import notifications  # noqa: F401  registers signal handlers
