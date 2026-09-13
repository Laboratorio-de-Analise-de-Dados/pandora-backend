import hashlib
import os
import zipfile

from django.db import migrations, models


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backfill_content_sha256(apps, schema_editor):
    """Hash por .fcs: extrai cada amostra do ZIP do experimento (best-effort).

    Linhas sem ZIP em disco ou com `fcs_path` legado usam o arquivo direto.
    Falhas ficam com ``content_sha256=None``.
    """
    FileDataModel = apps.get_model("fcs_parser", "FileDataModel")
    zip_cache = {}

    for fd in FileDataModel.objects.all().only(
        "id", "source_path", "fcs_path", "file_name", "experiment_id"
    ).select_related("experiment"):
        try:
            if fd.fcs_path and os.path.exists(fd.fcs_path):
                digest = _sha256(fd.fcs_path)
            else:
                zip_path = getattr(fd.experiment, "zip_path", None)
                if not zip_path or not os.path.exists(zip_path):
                    continue
                if zip_path not in zip_cache:
                    zip_cache[zip_path] = zipfile.ZipFile(zip_path)
                zf = zip_cache[zip_path]
                name = fd.source_path or fd.file_name
                with zf.open(name) as entry:
                    digest = hashlib.sha256(entry.read()).hexdigest()
            FileDataModel.objects.filter(pk=fd.pk).update(
                content_sha256=digest
            )
        except (OSError, KeyError, zipfile.BadZipFile):
            continue

    for zf in zip_cache.values():
        zf.close()


class Migration(migrations.Migration):

    dependencies = [
        ("fcs_parser", "0012_content_guid_sha256"),
    ]

    operations = [
        migrations.AddField(
            model_name="filedatamodel",
            name="content_sha256",
            field=models.CharField(
                blank=True, db_index=True, max_length=64, null=True
            ),
        ),
        migrations.RunPython(
            backfill_content_sha256, reverse_code=migrations.RunPython.noop
        ),
    ]
