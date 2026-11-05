from django.core.management.base import BaseCommand

from apps.integrations.accounting import push_all


class Command(BaseCommand):
    help = "Send confirmed payments to QuickBooks Online for every connected workspace."

    def handle(self, *args, **options):
        for conn_id, result in push_all().items():
            self.stdout.write(f"connection {conn_id}: {result}")
