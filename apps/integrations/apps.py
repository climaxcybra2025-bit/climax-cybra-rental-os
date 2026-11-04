from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    verbose_name = "Listings and integrations"

    def ready(self):
        from . import events  # noqa: F401  registers signal handlers
