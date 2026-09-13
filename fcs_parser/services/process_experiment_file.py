"""Unified service for processing experiment ZIP files into FileDataModels.

This module is the single place where the pipeline
ZIP → extract .fcs → parse → create FileDataModel + Parquet
lives.  Tasks and views delegate here instead of reimplementing it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import zipfile

import pandas as pd
import readfcs
from django.conf import settings

from fcs_parser.models import (
    ExperimentModel,
    FileDataModel,
    FileModel,
    SubsampleModel,
)
from fcs_parser.services.process_fcs import FCSResult, process_fcs_file

logger = logging.getLogger(__name__)


def _extract_dir(experiment_id: int) -> str:
    """Temporary directory for extracted .fcs files (ephemeral)."""
    return os.path.join(settings.MEDIA_ROOT, "fcs_files", str(experiment_id))


def file_sha256(path: str) -> str:
    """SHA-256 de um arquivo em disco, lido em blocos (blob identity)."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _content_guid(headers: dict | None) -> str | None:
    """GUID do .fcs vindo do header (readfcs normaliza para `guid`)."""
    if not headers:
        return None
    guid = headers.get("guid")
    return str(guid) if guid not in (None, "") else None


def assemble_chunks(experiment: ExperimentModel, extension: str = ".zip") -> str:
    """Concatenate uploaded chunks into the final file (.zip or .fcs).

    Returns the path to the assembled file.
    Raises ``ValueError`` if any chunk is missing.
    """
    chunk_dir = os.path.join(settings.MEDIA_ROOT, "chunks")
    final_name = f"{experiment.id}{extension}"
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


def subsample_for_path(
    experiment: ExperimentModel, relative_path: str
) -> SubsampleModel | None:
    """Subsample derivado do diretório do arquivo dentro do ZIP.

    Arquivos na raiz do ZIP não ganham subsample (``None``) — o cliente pode
    criar um e mover as amostras na UI. Diretórios aninhados viram um nome
    único com ``/`` (ex.: ``tempo_1/controles``).
    """
    directory = os.path.dirname(relative_path).replace(os.sep, "/").strip("/")
    if not directory:
        return None

    subsample, _ = SubsampleModel.objects.get_or_create(
        experiment=experiment,
        source_path=directory,
        defaults={"name": directory},
    )
    return subsample


def extract_metadata_from_zip(file_model: FileModel) -> list[str]:
    """Extract only metadata (headers + channel names) from each .fcs in the ZIP.

    Creates FileDataModel rows with ``parquet_path=None`` so that data is
    parsed lazily on first access via ``get_dataframe()``.

    This is lightweight: reads only the FCS header/text segment (no event data),
    keeping RAM usage minimal during upload.

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
            for file_name in sorted(files):
                if not file_name.endswith(".fcs"):
                    continue

                complete_path = os.path.join(root, file_name)
                relative_path = os.path.relpath(complete_path, directory_path)
                headers, _ = readfcs.view(complete_path)
                channels_df = readfcs.ReadFCS(complete_path).channels
                channel_names = channels_df["PnN"].tolist()

                if not values:
                    values = channel_names

                FileDataModel.objects.create(
                    headers=headers,
                    data_set=None,
                    experiment=experiment,
                    file_name=file_name,
                    source_path=relative_path.replace(os.sep, "/"),
                    content_guid=_content_guid(headers),
                    subsample=subsample_for_path(experiment, relative_path),
                    file=file_model,
                    parquet_path=None,
                )

        experiment.zip_path = zip_path
        experiment.values = values
        experiment.status = "done"
        experiment.save(update_fields=["zip_path", "values", "status"])

        logger.info(
            "Metadados do Experimento %s ('%s') extraídos com sucesso.",
            experiment.id,
            experiment.title,
        )
    finally:
        if os.path.isdir(directory_path):
            shutil.rmtree(directory_path, ignore_errors=True)
            logger.info("Diretório temporário '%s' removido.", directory_path)

    return values


def extract_metadata_from_fcs(file_model: FileModel) -> list[str]:
    """Metadados de um experimento enviado como um único .fcs (sem ZIP).

    Cria um único ``FileDataModel`` apontando para o .fcs no disco via
    ``fcs_path`` — sem `zip_path`, esse é o caminho que o
    ``FileDataModel.get_dataframe()`` usa para reconstruir o Parquet na
    primeira leitura.
    """
    experiment = file_model.experiment
    fcs_path = file_model.file.path

    headers, _ = readfcs.view(fcs_path)
    channels_df = readfcs.ReadFCS(fcs_path).channels
    values = channels_df["PnN"].tolist()

    FileDataModel.objects.create(
        headers=headers,
        data_set=None,
        experiment=experiment,
        file_name=file_model.file_name,
        source_path=file_model.file_name or "",
        content_guid=_content_guid(headers),
        file=file_model,
        fcs_path=fcs_path,
        parquet_path=None,
    )

    experiment.values = values
    experiment.status = "done"
    experiment.save(update_fields=["values", "status"])

    logger.info(
        "Metadados do Experimento %s ('%s') extraídos de um .fcs solto.",
        experiment.id,
        experiment.title,
    )

    return values


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
                relative_path = os.path.relpath(complete_path, directory_path)
                result: FCSResult = process_fcs_file(complete_path)

                if not values:
                    values = result.channels

                file_data = FileDataModel.objects.create(
                    headers=result.headers,
                    data_set=None,
                    experiment=experiment,
                    file_name=file_name,
                    source_path=relative_path.replace(os.sep, "/"),
                    content_guid=_content_guid(result.headers),
                    subsample=subsample_for_path(experiment, relative_path),
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

    ``file_name`` pode ser o caminho relativo dentro do ZIP
    (``tempo_1/a1.fcs``) — preferível, porque nomes repetidos em pastas
    diferentes são amostras distintas — ou só o nome do arquivo (legado).

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
            names = zf.namelist()
            # Caminho relativo casa exato; nome solto cai no sufixo (legado).
            matching = [n for n in names if n == file_name] or [
                n for n in names if n.endswith(f"/{file_name}")
            ]
            if not matching:
                return None
            zf.extract(matching[0], extract_dir)
            return os.path.join(extract_dir, matching[0])
    except (zipfile.BadZipFile, KeyError):
        logger.warning("Falha ao extrair '%s' do ZIP '%s'.", file_name, zip_path)
        return None
