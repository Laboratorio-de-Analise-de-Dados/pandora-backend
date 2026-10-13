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
from django.core.exceptions import SuspiciousFileOperation

from fcs_parser.models import (
    ExperimentModel,
    FileDataModel,
    FileModel,
    SubsampleModel,
)
from fcs_parser.services.process_fcs import FCSResult, process_fcs_file

logger = logging.getLogger(__name__)

# Teto de amostras por experimento (BE-42) — validado na borda do init.
MAX_EXPERIMENT_SAMPLES = 200


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


def assemble_chunks(upload_key: str, total_chunks: int, extension: str = ".zip") -> str:
    """Concatenate uploaded chunks into the final file (.zip or .fcs).

    ``upload_key`` namespacing: experiment id no fluxo de criação,
    ``f<file_model_id>`` no fluxo "adicionar arquivos".

    Returns the path to the assembled file.
    Raises ``ValueError`` if any chunk is missing.
    """
    chunk_dir = os.path.join(settings.MEDIA_ROOT, "chunks")
    final_name = f"{upload_key}{extension}"
    final_path = os.path.join(settings.MEDIA_ROOT, final_name)

    with open(final_path, "wb") as outfile:
        for i in range(total_chunks or 0):
            chunk_path = os.path.join(chunk_dir, f"{upload_key}_{i}.part")
            if not os.path.exists(chunk_path):
                raise ValueError(f"Chunk {i} faltando")
            with open(chunk_path, "rb") as f:
                outfile.write(f.read())
            os.remove(chunk_path)

    return final_path


def wrap_fcs_as_zip(fcs_path: str, fcs_name: str, zip_path: str) -> str:
    """Aglutina um `.fcs` solto num ZIP — a unidade física é sempre ZIP.

    O `.fcs` dentro é byte-a-byte o enviado; o hash do upload vive em
    ``FileDataModel.content_sha256``.
    """
    os.makedirs(os.path.dirname(zip_path) or ".", exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(fcs_path, arcname=os.path.basename(fcs_name))
    os.remove(fcs_path)
    return zip_path


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


def _already_in_experiment(experiment, guid, sha256) -> bool:
    """Dedup por experimento: a amostra já existe por guid ou hash."""
    qs = FileDataModel.objects.filter(experiment=experiment)
    if guid:
        if qs.filter(content_guid=guid).exists():
            return True
    if sha256:
        if qs.filter(content_sha256=sha256).exists():
            return True
    return False


def _existing_sample_names(experiment) -> tuple[set, set]:
    """Identidades de nome já ocupadas por amostras ativas do experimento.

    Retorna ``(paths, root_names)``: paths são os ``source_path`` exatos
    (a identidade real da amostra dentro do ZIP); root_names são os
    ``file_name`` de amostras que vivem na raiz (sem pasta) — inclui
    linhas legadas com ``source_path`` vazio.
    """
    paths: set = set()
    root_names: set = set()
    for file_name, source_path in FileDataModel.objects.filter(
        experiment=experiment, active=True
    ).values_list("file_name", "source_path"):
        if source_path:
            paths.add(source_path)
        if not source_path or "/" not in source_path:
            if file_name:
                root_names.add(file_name)
    return paths, root_names


def _sample_name_taken(
    paths: set, root_names: set, relative_path: str, file_name: str
) -> bool:
    """Nome/colisão de amostra: mesmo caminho, ou mesmo nome na raiz.

    Dois arquivos com o mesmo ``file_name`` em pastas diferentes dentro
    do ZIP são amostras legítimas e distintas — não colidem. Colidem o
    mesmo ``source_path`` ou dois nomes iguais na raiz do experimento
    (o caso comum do lote de tubos soltos).
    """
    if relative_path in paths:
        return True
    return "/" not in relative_path and file_name in root_names


def extract_metadata_from_zip(
    file_model: FileModel,
    skipped: list | None = None,
    name_conflicts: list | None = None,
) -> list[str]:
    """Extract only metadata (headers + channel names) from each .fcs in the ZIP.

    Creates FileDataModel rows with ``parquet_path=None`` so that data is
    parsed lazily on first access via ``get_dataframe()``.

    This is lightweight: reads only the FCS header/text segment (no event data),
    keeping RAM usage minimal during upload.

    Amostras já presentes no experimento (mesmo ``content_guid`` ou
    ``content_sha256``) são puladas e reportadas em ``skipped`` — dedup é
    por experimento, nunca bloqueia o upload.

    Nome de amostra já ocupado (mesmo ``source_path`` ou mesmo nome na
    raiz do experimento) com conteúdo diferente vai para
    ``name_conflicts`` (BE-42): a amostra não é criada e o chamador
    decide como reportar — colisão silenciosa nunca acontece.

    Returns the list of channel names (``values``) found in the first file.
    """
    experiment = file_model.experiment
    zip_path = upload_file_fs_path(file_model)
    directory_path = _extract_dir(experiment.id)

    os.makedirs(directory_path, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(directory_path)

    values: list[str] = []
    taken_paths, taken_root_names = _existing_sample_names(experiment)

    try:
        for root, _dirs, files in os.walk(directory_path):
            for file_name in sorted(files):
                if not file_name.endswith(".fcs"):
                    continue

                complete_path = os.path.join(root, file_name)
                relative_path = os.path.relpath(complete_path, directory_path).replace(
                    os.sep, "/"
                )
                headers, _ = readfcs.view(complete_path)
                content_sha = file_sha256(complete_path)
                guid = _content_guid(headers)

                if _already_in_experiment(experiment, guid, content_sha):
                    if skipped is not None:
                        skipped.append(relative_path)
                    logger.info(
                        "Amostra '%s' já existe no experimento %s — pulada.",
                        relative_path,
                        experiment.id,
                    )
                    continue

                if _sample_name_taken(
                    taken_paths, taken_root_names, relative_path, file_name
                ):
                    if name_conflicts is not None:
                        name_conflicts.append(relative_path)
                    logger.warning(
                        "Nome de amostra '%s' já existe no experimento %s — "
                        "colisão de nome (conteúdo diferente), não criada.",
                        relative_path,
                        experiment.id,
                    )
                    continue

                channels_df = readfcs.ReadFCS(complete_path).channels
                channel_names = channels_df["PnN"].tolist()

                if not values:
                    values = channel_names

                FileDataModel.objects.create(
                    headers=headers,
                    data_set=None,
                    experiment=experiment,
                    file_name=file_name,
                    source_path=relative_path,
                    content_guid=guid,
                    content_sha256=content_sha,
                    subsample=subsample_for_path(experiment, relative_path),
                    file=file_model,
                    parquet_path=None,
                )
                taken_paths.add(relative_path)
                if "/" not in relative_path:
                    taken_root_names.add(file_name)

        if experiment.status != "done" or not experiment.zip_path:
            experiment.zip_path = experiment.zip_path or zip_path
            experiment.values = values or experiment.values
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
    zip_path = upload_file_fs_path(file_model)
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
                    content_sha256=file_sha256(complete_path),
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


def upload_file_fs_path(file_model: "FileModel") -> str | None:
    """Resolve o caminho do ZIP do upload em disco.

    Linhas antigas gravaram o path absoluto no FileField (`file.name`) —
    o correto é o nome relativo ao MEDIA_ROOT. Tolera os dois formatos:
    path absoluto é usado direto; caso contrário resolve via storage.
    """
    file_field = getattr(file_model, "file", None)
    name = getattr(file_field, "name", None) if file_field else None
    if not name:
        return None
    if os.path.isabs(name):
        return name if os.path.exists(name) else None
    try:
        return file_field.path
    except (ValueError, SuspiciousFileOperation):
        return None


def extract_fcs_from_zip(upload: "FileModel", file_name: str) -> str | None:
    """Extract a single .fcs from the upload's ZIP (on-demand).

    A resolução é por amostra: cada ``FileDataModel`` aponta pro seu
    ``FileModel`` (upload), então o ZIP correto é o dele — não um
    ``zip_path`` único do experimento.

    ``file_name`` pode ser o caminho relativo dentro do ZIP
    (``tempo_1/a1.fcs``) — preferível, porque nomes repetidos em pastas
    diferentes são amostras distintas — ou só o nome do arquivo (legado).

    Returns the path to the extracted file inside a temp directory,
    or ``None`` if the ZIP or entry is not found.
    The caller is responsible for cleaning up the file after use.
    """
    # ZIP ausente/irresolúvel vira "not found" em vez de derrubar o rebuild.
    zip_path = upload_file_fs_path(upload)
    if not zip_path or not os.path.exists(zip_path):
        return None

    extract_dir = _extract_dir(upload.experiment_id)
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
