"""Management command para redescobrir o caminho das amostras pelo ZIP.

Usage:
    python manage.py repair_source_path --dry-run
    python manage.py repair_source_path --experiment 12 --recreate

Sem ``--recreate`` o comando só remapeia o que é inequívoco. Com ``--recreate``
ele também resolve nomes repetidos no ZIP, inativando as amostras ambíguas e
criando uma nova por entrada — os gates das antigas ficam com as inativadas.
"""

from django.core.management.base import BaseCommand

from fcs_parser.models import ExperimentModel
from fcs_parser.services.repair_source_path import repair_experiment_source_paths


class Command(BaseCommand):
    help = "Redescobre o source_path das amostras a partir do ZIP do experimento."

    def add_arguments(self, parser):
        parser.add_argument(
            "--experiment",
            type=int,
            action="append",
            dest="experiments",
            help="Limita a um experimento (pode repetir). Padrão: todos.",
        )
        parser.add_argument(
            "--recreate",
            action="store_true",
            help="Inativa amostras ambíguas e recria a partir do ZIP (perde as análises delas).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Só relata o que faria, sem gravar nada.",
        )

    def handle(self, *args, **options):
        experiments = ExperimentModel.objects.exclude(zip_path__isnull=True).exclude(
            zip_path=""
        )
        if options["experiments"]:
            experiments = experiments.filter(id__in=options["experiments"])

        recreate = options["recreate"]
        dry_run = options["dry_run"]

        for experiment in experiments.order_by("id"):
            report = repair_experiment_source_paths(
                experiment, recreate=recreate, dry_run=dry_run
            )
            if not (report.remapped or report.ambiguous or report.missing):
                continue

            self.stdout.write(f"Experimento {experiment.id} – {experiment.title}")
            for file_data_id, path in report.remapped:
                self.stdout.write(f"  remapeada  amostra {file_data_id} -> {path}")
            for path in report.recreated:
                self.stdout.write(f"  recriada   {path}")
            for file_data_id in report.deactivated:
                self.stdout.write(f"  inativada  amostra {file_data_id}")
            for path in report.ambiguous:
                if path not in report.recreated:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ambígua    {path} (use --recreate para resolver)"
                        )
                    )
            for path in report.missing:
                self.stdout.write(
                    self.style.WARNING(f"  sem linha  {path} (está no ZIP, não no banco)")
                )

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry run: nada foi gravado."))
        else:
            self.stdout.write(self.style.SUCCESS("Reparo concluído."))
