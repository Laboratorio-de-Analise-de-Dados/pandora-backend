from django.core.exceptions import ValidationError
import os
import re

# Um experimento pode ser enviado como um ZIP com vários .fcs ou como um único
# .fcs solto.
EXPERIMENT_FILE_EXTENSIONS = (".zip", ".fcs")
INVALID_EXTENSION_MESSAGE = "Envie um arquivo .zip ou .fcs."


def experiment_file_extension(file_name) -> str:
    """Extensão normalizada de *file_name*, validando .zip/.fcs.

    Levanta ``ValidationError`` quando o nome está ausente ou a extensão não é
    suportada. O upload é feito em chunks, então esta é a única chance de
    validar o arquivo pelo nome informado pelo cliente.
    """
    if not isinstance(file_name, str) or not file_name.strip():
        raise ValidationError(INVALID_EXTENSION_MESSAGE)

    ext = os.path.splitext(file_name.strip())[1].lower()
    if ext not in EXPERIMENT_FILE_EXTENSIONS:
        raise ValidationError(INVALID_EXTENSION_MESSAGE)
    return ext


_WINDOWS_DRIVE = re.compile(r"^[a-zA-Z]:")
INVALID_RELATIVE_PATH_MESSAGE = (
    "relativePath inválido — envie um caminho relativo simples."
)
RELATIVE_PATH_BASENAME_MESSAGE = (
    "relativePath precisa terminar no mesmo nome de fileName."
)


def sanitize_relative_path(relative_path, file_name) -> str:
    """Caminho relativo de um `.fcs` solto → arcname no ZIP aglutinado (BE-49).

    Só diretórios relativos e limpos: sem ``..``, sem separador inicial,
    sem drive Windows e sem segmentos vazios. O basename precisa bater
    com ``file_name`` — mesma proteção de lote do ``fileName`` em chunk.
    """
    normalized = str(relative_path or "").replace("\\", "/").strip()
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or _WINDOWS_DRIVE.match(normalized)
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValidationError(INVALID_RELATIVE_PATH_MESSAGE)
    if parts[-1] != file_name:
        raise ValidationError(RELATIVE_PATH_BASENAME_MESSAGE)
    return normalized
