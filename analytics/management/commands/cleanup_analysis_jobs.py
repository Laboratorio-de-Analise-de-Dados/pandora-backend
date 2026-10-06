"""Management command to purge old finished analysis jobs (BE-27).

Removes `done` jobs older than the retention window — the row is the
operational queue record; the artifacts it produced (gates, revisions,
checkpoints) live on independently. `error`/`quarantine` are kept for
manual review — quarantine is never auto-purged.

Usage:
    python manage.py cleanup_analysis_jobs [--days 14] [--dry-run]

Schedule via host cron, e.g.:
    0 4 * * * cd /app && python manage.py cleanup_analysis_jobs
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from analytics.models import AnalysisJob


class Command(BaseCommand):
    help = "Remove analysis jobs done mais antigos que a retenção."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=settings.JUVIA_JOB_RETENTION_DAYS,
            help="Retenção em dias de jobs concluídos (default: %(default)s).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Só conta o que seria removido, sem deletar.",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["days"])
        qs = AnalysisJob.objects.filter(
            status=AnalysisJob.STATUS_DONE,
            finished_at__lt=cutoff,
        )
        count = qs.count()
        if options["dry_run"]:
            self.stdout.write(
                f"[dry-run] {count} job(s) done anteriores a "
                f"{cutoff.date()} seriam removidos."
            )
            return
        qs.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"{count} job(s) done anteriores a {cutoff.date()} removidos."
            )
        )
