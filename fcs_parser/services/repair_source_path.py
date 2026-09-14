"""Reconstrói o caminho de origem das amostras a partir do ZIP do experimento.

Amostras criadas antes do ``source_path`` guardavam só o nome do arquivo, então
duas amostras homônimas em pastas diferentes do ZIP (``tempo_1/a1.fcs`` e
``tempo_2/a1.fcs``) são indistinguíveis no banco. O ZIP continua no disco e é a
fonte de verdade, então o caminho pode ser redescoberto a partir dele:

* nome que aparece **uma única vez** no ZIP é remapeado direto, preservando
  gates e Parquet da amostra;
* nome **repetido** no ZIP é ambíguo — não há como saber qual linha do banco é
  qual pasta. Nesse caso o reparo só acontece com ``recreate=True``: as linhas
  ambíguas são inativadas e uma amostra nova é criada por entrada do ZIP. As
  análises feitas nas linhas antigas ficam presas às amostras inativas, o que
  é aceitável no v0.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field

import readfcs
from django.db import transaction
from django.utils import timezone

from fcs_parser.models import ExperimentModel, FileDataModel
from fcs_parser.services.process_experiment_file import subsample_for_path

logger = logging.getLogger(__name__)


@dataclass
class RepairReport:
    """Resultado do reparo de um experimento."""

    experiment_id: int
    remapped: list[tuple[int, str]] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    recreated: list[str] = field(default_factory=list)
    deactivated: list[int] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _fcs_entries(zip_path: str) -> list[str]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        return [
            name
            for name in zf.namelist()
            if name.endswith(".fcs") and not name.endswith("/")
        ]


def _read_headers(zip_path: str, entry: str) -> dict:
    """Metadados da entrada do ZIP, sem carregar os eventos."""
    extract_dir = tempfile.mkdtemp(prefix="pandora-repair-")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            extracted = zf.extract(entry, extract_dir)
        headers, _ = readfcs.view(extracted)
        return headers
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def repair_experiment_source_paths(
    experiment: ExperimentModel, *, recreate: bool = False, dry_run: bool = False
) -> RepairReport:
    """Redescobre o ``source_path`` das amostras de *experiment* pelo ZIP."""
    report = RepairReport(experiment_id=experiment.id)

    zip_path = experiment.zip_path
    if not zip_path or not os.path.exists(zip_path):
        return report

    entries = _fcs_entries(zip_path)
    by_name: dict[str, list[str]] = {}
    for entry in entries:
        by_name.setdefault(os.path.basename(entry), []).append(entry)

    rows = list(FileDataModel.objects.filter(experiment=experiment).order_by("id"))
    valid = {row.source_path for row in rows if row.source_path in entries}
    stale = [row for row in rows if row.source_path not in entries]

    for name, paths in sorted(by_name.items()):
        free = [path for path in paths if path not in valid]
        pending = [row for row in stale if (row.file_name or "") == name]
        if not pending or not free:
            continue

        if len(free) == 1 and len(pending) == 1:
            row = pending[0]
            report.remapped.append((row.id, free[0]))
            if not dry_run:
                with transaction.atomic():
                    row.source_path = free[0]
                    row.subsample = subsample_for_path(experiment, free[0])
                    row.save(update_fields=["source_path", "subsample"])
            continue

        report.ambiguous.extend(free)
        if not recreate:
            continue

        _recreate(experiment, zip_path, pending, free, report, dry_run=dry_run)

    for entry in entries:
        if entry not in valid and entry not in report.ambiguous:
            if not any(path == entry for _, path in report.remapped):
                report.missing.append(entry)

    return report


def _recreate(
    experiment: ExperimentModel,
    zip_path: str,
    pending: list[FileDataModel],
    free: list[str],
    report: RepairReport,
    *,
    dry_run: bool,
) -> None:
    """Inativa as linhas ambíguas e recria uma amostra por entrada do ZIP."""
    report.deactivated.extend(row.id for row in pending)
    report.recreated.extend(free)

    if dry_run:
        return

    now = timezone.now()
    file_model = pending[0].file

    with transaction.atomic():
        for row in pending:
            row.active = False
            row.deactivated_at = now
            row.save(update_fields=["active", "deactivated_at"])

        for entry in free:
            FileDataModel.objects.create(
                headers=_read_headers(zip_path, entry),
                data_set=None,
                experiment=experiment,
                file_name=os.path.basename(entry),
                source_path=entry,
                subsample=subsample_for_path(experiment, entry),
                file=file_model,
                parquet_path=None,
            )

    logger.info(
        "Experimento %s: %s amostra(s) ambígua(s) inativada(s), %s recriada(s).",
        experiment.id,
        len(pending),
        len(free),
    )
