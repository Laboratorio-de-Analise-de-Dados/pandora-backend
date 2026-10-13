"""Normaliza paths de arquivo gravados no banco: absoluto → relativo ao MEDIA_ROOT.

Paths absolutos só resolvem no filesystem do processo que escreveu — um
backend em outro filesystem (container, host, worktree) lia o campo,
não achava o arquivo e caía no fallback vazio. Com path relativo, cada
backend resolve `MEDIA_ROOT + rel` localmente e a linha fica portátil.

O resolver (`fcs_parser.models.resolve_media_path`) continua aceitando
absoluto — linhas que a heurística aqui não reconhecer passam direto e
mantêm o comportamento antigo.
"""

import posixpath

from django.db import migrations


def _tail_after(path: str, anchor: str) -> str:
    """Devolve o trecho do path a partir de `anchor` (incluindo ela)."""
    idx = path.find(anchor)
    return path[idx:] if idx >= 0 else posixpath.basename(path)


def relativize_media_paths(apps, schema_editor):
    FileDataModel = apps.get_model("fcs_parser", "FileDataModel")
    FileModel = apps.get_model("fcs_parser", "FileModel")

    # file_data.parquet_path é sempre <…>/parquet/<id>.parquet
    for fd in FileDataModel.objects.exclude(parquet_path__isnull=True).exclude(
        parquet_path=""
    ):
        p = fd.parquet_path
        if p.startswith("/"):
            fd.parquet_path = _tail_after(p, "parquet/")
            fd.save(update_fields=["parquet_path"])

    # file_data.fcs_path é <…>/fcs_files/<experiment_id>/<nome>.fcs —
    # precisa preservar a subpasta do experimento
    for fd in FileDataModel.objects.exclude(fcs_path__isnull=True).exclude(
        fcs_path=""
    ):
        p = fd.fcs_path
        if p.startswith("/"):
            fd.fcs_path = _tail_after(p, "fcs_files/")
            fd.save(update_fields=["fcs_path"])

    # experiment_files.file (FileField, upload_to="") — o nome é relativo
    # à raiz do MEDIA_ROOT: basename basta (ex.: "13.zip")
    for up in FileModel.objects.exclude(file__isnull=True).exclude(file=""):
        name = up.file.name
        if name.startswith("/"):
            up.file.name = posixpath.basename(name)
            up.save(update_fields=["file"])


def noop_reverse(apps, schema_editor):
    # Relativo → absoluto exigiria o MEDIA_ROOT do writer original, que é
    # justamente a informação que a migration apaga de propósito.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("fcs_parser", "0022_filedatamodel_control_channel_and_more"),
    ]

    operations = [
        migrations.RunPython(relativize_media_paths, noop_reverse),
    ]
