"""Unified service for processing experiment ZIP files into FileDataModels.

This module is the single place where the pipeline
ZIP → extract .fcs → parse → create FileDataModel + Parquet
lives.  Tasks and views delegate here instead of reimplementing it.
"""

from __future__ import annotations

import logging
import os
import shutil
import zipfile

import pandas as pd
from django.conf import settings

from fcs_parser.models import ExperimentModel, FileDataModel, FileModel
from fcs_parser.services.process_fcs import FCSResult, process_fcs_file

logger = logging.getLogger(__name__)


def _extract_dir(experiment_id: int) -> str:
    """Temporary directory for extracted .fcs files (ephemeral)."""
    return os.path.join(settings.MEDIA_ROOT, "fcs_files", str(experiment_id))


def assemble_chunks(experiment: ExperimentModel) -> str:
    """Concatenate uploaded chunks into the final ZIP file.

    Returns the path to the assembled ZIP.
    Raises ``ValueError`` if any chunk is missing.
    """
    chunk_dir = os.path.join(settings.MEDIA_ROOT, "chunks")
    final_name = f"{experiment.id}.zip"
    final_path = os.path.join(settings.MEDIA_ROOT, final_name)

    with open(final_path, "wb") as outfile:
        for i in range(experiment.total_chunks):
            chunk_path = os.path.join(chunk_dir, f"{experiment.id}_{i}.part")
            if not os.path.exists(chunk_path):
                raise ValueError(f"Chunk {i} faltando")
            with open(chunk_path, "rb") as f:
                outfile.write(f.read())
            os.remove(chunk_path)

    return final_path


def process_experiment_zip(file_model: FileModel) -> list[str]:
    """Process the ZIP attached to *file_model*, creating FileDataModels.

    This is the **single implementation** of the pipeline:
    1. Extract ZIP → temp dir
    2. For each .fcs inside, parse and create a ``FileDataModel``
       with a Parquet cache.
    3. Store the ZIP path on the ``ExperimentModel`` (source of truth).
    4. Clean up the temp extraction directory (the .fcs files are ephemeral).

    Returns the list of channel names (``values``) found in the first file.
    """
    experiment = file_model.experiment
    zip_path = file_model.file.path
    directory_path = _extract_dir(experiment.id)

    os.makedirs(directory_path, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(directory_path)

    values: list[str] = []

    try:
        for root, _dirs, files in os.walk(directory_path):
            for file_name in files:
                if not file_name.endswith(".fcs"):
                    continue

                complete_path = os.path.join(root, file_name)
                result: FCSResult = process_fcs_file(complete_path)

                if not values:
                    values = result.channels

                file_data = FileDataModel.objects.create(
                    headers=result.headers,
                    data_set=None,
                    experiment=experiment,
                    file_name=file_name,
                    file=file_model,
                )
                file_data.save_dataframe(pd.DataFrame(result.data))

        # Persist the ZIP path as the experiment's source of truth.
        experiment.zip_path = zip_path
        experiment.values = values
        experiment.status = "done"
        experiment.save(update_fields=["zip_path", "values", "status"])

        logger.info(
            "Processamento do Experimento %s ('%s') concluído.",
            experiment.id,
            experiment.title,
        )
    finally:
        # The extracted .fcs directory is ephemeral — always clean up.
        if os.path.isdir(directory_path):
            shutil.rmtree(directory_path, ignore_errors=True)
            logger.info("Diretório temporário '%s' removido.", directory_path)

    return values


def extract_fcs_from_zip(experiment: ExperimentModel, file_name: str) -> str | None:
    """Extract a single .fcs from the experiment's ZIP (on-demand).

    Returns the path to the extracted file inside a temp directory,
    or ``None`` if the ZIP or entry is not found.
    The caller is responsible for cleaning up the file after use.
    """
    zip_path = getattr(experiment, "zip_path", None)
    if not zip_path or not os.path.exists(zip_path):
        return None

    extract_dir = _extract_dir(experiment.id)
    os.makedirs(extract_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Find the entry matching file_name (may be nested in subdirs).
            matching = [n for n in zf.namelist() if n.endswith(file_name)]
            if not matching:
                return None
            zf.extract(matching[0], extract_dir)
            return os.path.join(extract_dir, matching[0])
    except (zipfile.BadZipFile, KeyError):
        logger.warning("Falha ao extrair '%s' do ZIP '%s'.", file_name, zip_path)
        return None
