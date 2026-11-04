from datetime import date

from django.core.management.base import BaseCommand

from apps.integrations.jobs import run_daily_all


class Command(BaseCommand):
    help = "Daily jobs per workspace: check-ins, checkouts with turnover cleans, lease-expiry alerts."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Run for this date (YYYY-MM-DD) instead of today")
        parser.add_argument("--force", action="store_true", help="Run before the configured hour")

    def handle(self, *args, **options):
        day = date.fromisoformat(options["date"]) if options["date"] else None
        for slug, result in run_daily_all(day=day, force=options["force"] or bool(day)).items():
            self.stdout.write(f"{slug}: {result or 'nothing to do (already ran or too early)'}")
