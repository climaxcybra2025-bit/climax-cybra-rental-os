"""One long-running process for all background work, so no Celery or Redis is needed.

Every minute: deliver n8n events. Every SCHEDULER_SYNC_MINUTES: sync Hostaway and send
confirmed payments to QuickBooks.
Every 10 minutes: daily jobs (each runs once per workspace-local day).
"""

import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.integrations.accounting import push_all
from apps.integrations.events import deliver_due
from apps.integrations.jobs import run_daily_all, sync_all_channels

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run background jobs forever (use as its own container or service)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run every job once and exit")

    def handle(self, *args, **options):
        self.running = True
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        last = {"sync": 0.0, "daily": 0.0}
        while self.running:
            close_old_connections()
            now = time.monotonic()
            self._safe("deliver", deliver_due)
            if options["once"] or now - last["sync"] >= settings.SCHEDULER_SYNC_MINUTES * 60:
                self._safe("sync", sync_all_channels)
                self._safe("quickbooks", push_all)
                last["sync"] = now
            if options["once"] or now - last["daily"] >= 600:
                self._safe("daily", run_daily_all)
                last["daily"] = now
            if options["once"]:
                break
            for _ in range(60):
                if not self.running:
                    break
                time.sleep(1)

    def _safe(self, name, fn):
        try:
            result = fn()
            logger.info("scheduler %s: %s", name, result)
        except Exception:
            logger.exception("scheduler %s failed", name)

    def _stop(self, *_):
        self.running = False
