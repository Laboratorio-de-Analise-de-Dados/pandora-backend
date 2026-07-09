"""Standalone utility functions (formerly Celery tasks).

These functions are now called synchronously or via management commands.
No Celery dependency required.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import timedelta

import pandas as pd
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import ExperimentModel, FileDataModel, FileModel, parquet_storage_dir

logger = logging.getLogger(__name__)


def cleanup_cold_parquet(max_idle_days: int = 7) -> int:
    """Remove regenerable Parquet files: orphans and cold (idle > max_idle_days).

    The ZIP is the source of truth, so Parquet can always be rebuilt via
    ``get_dataframe`` -> re-extract from ZIP -> reparse.
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

    logger.info("cleanup_cold_parquet removeu %d arquivo(s) Parquet.", removed)
    return removed


def cleanup_ephemeral_fcs() -> int:
    """Remove leftover extraction directories (fcs_files/).

    These are normally cleaned up by ``process_experiment_zip``, but may
    linger after crashes or interrupted processing.
    """
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

    logger.info("cleanup_ephemeral_fcs removeu %d diretório(s).", removed)
    return removed
