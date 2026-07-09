"""Management command to clean up cold/orphan Parquet files.

Usage:
    python manage.py cleanup_parquet [--max-idle-days 7]

Replaces the former Celery Beat scheduled task. Add to cron:
    0 3 * * 0 cd /app && python manage.py cleanup_parquet
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from fcs_parser.tasks import cleanup_cold_parquet, cleanup_ephemeral_fcs


class Command(BaseCommand):
    help = "Remove orphan and cold Parquet cache files (regenerable from ZIP)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-idle-days",
            type=int,
            default=settings.PARQUET_MAX_IDLE_DAYS,
            help="Days of inactivity before a Parquet file is considered cold (default: %(default)s).",
        )

    def handle(self, *args, **options):
        max_idle_days = options["max_idle_days"]

        parquet_removed = cleanup_cold_parquet(max_idle_days=max_idle_days)
        self.stdout.write(f"Parquet cleanup: {parquet_removed} file(s) removed.")

        fcs_removed = cleanup_ephemeral_fcs()
        self.stdout.write(f"Ephemeral FCS cleanup: {fcs_removed} directory(ies) removed.")

        self.stdout.write(self.style.SUCCESS("Cleanup completed."))
