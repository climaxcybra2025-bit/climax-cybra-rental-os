from django.core.management.base import BaseCommand

from apps.integrations.jobs import sync_all_channels


class Command(BaseCommand):
    help = "Pull recent bookings and guest messages from every enabled Hostaway connection."

    def handle(self, *args, **options):
        for conn_id, result in sync_all_channels().items():
            self.stdout.write(f"connection {conn_id}: {result}")
