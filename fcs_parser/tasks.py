from __future__ import annotations

import logging
import os
import traceback
from datetime import timedelta

import pandas as pd
from celery import shared_task
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import ExperimentModel, FileDataModel, FileModel, parquet_storage_dir

logger = logging.getLogger(__name__)


@shared_task
def process_experiment_files_task(file_id: int):
    """Process FCS files asynchronously for a given FileModel.

    Delegates entirely to the unified service
    :func:`fcs_parser.services.process_experiment_zip`.
    """
    from fcs_parser.services.process_experiment_file import process_experiment_zip

    file_model: FileModel | None = None
    experiment: ExperimentModel | None = None

    try:
        file_model = get_object_or_404(FileModel, id=file_id)
        experiment = file_model.experiment

        if experiment.status == "processing":
            logger.info(
                "Experimento %s já está em processamento, ignorando.", experiment.id
            )
            return

        experiment.status = "processing"
        experiment.save(update_fields=["status"])

        process_experiment_zip(file_model)

    except Exception as e:
        logger.error(
            "Erro durante processamento do Experimento %s: %s",
            experiment.id if experiment else "?",
            e,
            exc_info=True,
        )
        if experiment is not None:
            experiment.refresh_from_db()
            experiment.status = "error"
            experiment.error_info = {
                "error_message": str(e),
                "details": traceback.format_exc(),
            }
            experiment.save(update_fields=["status", "error_info"])


@shared_task
def cleanup_cold_parquet_task(max_idle_days: int = 7):
    """Remove regenerable Parquet files: orphans and cold (idle > max_idle_days).

    The ZIP is the source of truth, so Parquet can always be rebuilt via
    ``get_dataframe`` → re-extract from ZIP → reparse.
    """
    removed = 0
    parquet_dir = parquet_storage_dir()

    # 1) Orphans: .parquet files on disk not referenced by any row.
    if os.path.isdir(parquet_dir):
        referenced = set(
            FileDataModel.objects.exclude(parquet_path__isnull=True)
            .exclude(parquet_path="")
            .values_list("parquet_path", flat=True)
        )
        for name in os.listdir(parquet_dir):
            full = os.path.join(parquet_dir, name)
            if os.path.isfile(full) and full not in referenced:
                try:
                    os.remove(full)
                    removed += 1
                except OSError:
                    pass

    # 2) Cold: rows whose Parquet hasn't been accessed in a while
    #    and whose experiment has a ZIP to rebuild from.
    cutoff = timezone.now() - timedelta(days=max_idle_days)
    cold = (
        FileDataModel.objects.exclude(parquet_path__isnull=True)
        .exclude(parquet_path="")
        .filter(last_accessed__lt=cutoff)
        .select_related("experiment")
    )
    for file_data in cold:
        # Only remove if the experiment has a ZIP to rebuild from.
        if not getattr(file_data.experiment, "zip_path", None):
            continue
        path = file_data.parquet_path
        if path and os.path.exists(path):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                continue
        FileDataModel.objects.filter(pk=file_data.pk).update(parquet_path=None)

    logger.info("cleanup_cold_parquet_task removeu %d arquivo(s) Parquet.", removed)
    return removed


@shared_task
def recompute_file_data_task(file_data_id: int):
    """Regenerate Parquet cache from the experiment's ZIP (source of truth).

    Extracts the specific .fcs from the ZIP, reparses it, saves as Parquet,
    invalidates Redis density cache, and recalculates gate analysis.
    """
    from analytics.models import GateModel
    from analytics.tasks import recalculate_gate_analysis_task
    from fcs_parser.services.process_experiment_file import extract_fcs_from_zip
    from fcs_parser.services.process_fcs import process_fcs_file
    from utils.density import invalidate_density

    file_data = get_object_or_404(FileDataModel, id=file_data_id)
    experiment = file_data.experiment

    # Try to extract the .fcs from the ZIP (source of truth).
    fcs_path = extract_fcs_from_zip(experiment, file_data.file_name)
    if fcs_path is None:
        logger.warning(
            "recompute_file_data_task: não foi possível extrair '%s' do ZIP "
            "para FileData %s.",
            file_data.file_name,
            file_data_id,
        )
        return {"status": "skipped", "reason": "fcs_not_found_in_zip"}

    try:
        result = process_fcs_file(fcs_path)
        file_data.save_dataframe(pd.DataFrame(result.data))
    finally:
        # Clean up the ephemeral extracted file.
        if os.path.exists(fcs_path):
            os.remove(fcs_path)

    invalidate_density(file_data_id)
    for gate_id in GateModel.objects.filter(file_data_id=file_data_id).values_list(
        "id", flat=True
    ):
        recalculate_gate_analysis_task.delay(gate_id)

    logger.info("recompute_file_data_task regenerou FileData %s.", file_data_id)
    return {"status": "ok", "file_data_id": file_data_id}


@shared_task
def cleanup_ephemeral_fcs_task():
    """Remove leftover extraction directories (fcs_files/).

    These are normally cleaned up by ``process_experiment_zip``, but may
    linger after crashes or interrupted processing.
    """
    import shutil

    fcs_root = os.path.join(settings.MEDIA_ROOT, "fcs_files")
    if not os.path.isdir(fcs_root):
        return 0

    removed = 0
    for name in os.listdir(fcs_root):
        full = os.path.join(fcs_root, name)
        if os.path.isdir(full):
            try:
                shutil.rmtree(full)
                removed += 1
            except OSError:
                pass

    logger.info("cleanup_ephemeral_fcs_task removeu %d diretório(s).", removed)
    return removed
