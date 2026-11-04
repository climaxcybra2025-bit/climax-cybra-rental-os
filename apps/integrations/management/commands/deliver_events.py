from django.core.management.base import BaseCommand

from apps.integrations.events import deliver_due


class Command(BaseCommand):
    help = "Send queued events to n8n webhooks (retries failures with backoff)."

    def handle(self, *args, **options):
        sent, failed = deliver_due()
        self.stdout.write(f"sent={sent} failed={failed}")
