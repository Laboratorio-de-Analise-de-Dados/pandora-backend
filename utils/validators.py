from django.core.exceptions import ValidationError
import os

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
