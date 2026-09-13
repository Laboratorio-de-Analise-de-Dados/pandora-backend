import hashlib
import os

from django.db import migrations, models


def backfill_content_guid(apps, schema_editor):
    """Promove o `guid` dos headers persistidos para a coluna própria."""
    FileDataModel = apps.get_model("fcs_parser", "FileDataModel")
    for file_data in FileDataModel.objects.exclude(headers__isnull=True).only(
        "id", "headers"
    ):
        guid = (file_data.headers or {}).get("guid")
        if guid not in (None, ""):
            FileDataModel.objects.filter(pk=file_data.pk).update(
                content_guid=str(guid)
            )


def backfill_sha256(apps, schema_editor):
    """Hash dos blobs já em disco, best-effort.

    Arquivos ausentes ou ilegíveis ficam com ``sha256=None`` e são cobertos
    quando um upload futuro cair no mesmo caminho.
    """
    FileModel = apps.get_model("fcs_parser", "FileModel")
    for fm in FileModel.objects.all().only("id", "file"):
        name = getattr(fm.file, "name", None)
        if not name:
            continue
        path = fm.file.path
        if not os.path.exists(path):
            continue
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError:
            continue
        FileModel.objects.filter(pk=fm.pk).update(sha256=digest.hexdigest())


class Migration(migrations.Migration):

    dependencies = [
        ("fcs_parser", "0011_backfill_source_path"),
    ]

    operations = [
        migrations.AddField(
            model_name="filemodel",
            name="sha256",
            field=models.CharField(
                blank=True, db_index=True, max_length=64, null=True
            ),
        ),
        migrations.AddField(
            model_name="filedatamodel",
            name="content_guid",
            field=models.CharField(
                blank=True, db_index=True, max_length=256, null=True
            ),
        ),
        migrations.AddConstraint(
            model_name="filedatamodel",
            constraint=models.UniqueConstraint(
                condition=~models.Q(content_guid__isnull=True)
                & ~models.Q(content_guid=""),
                fields=("experiment", "content_guid"),
                name="unique_content_guid_per_experiment",
            ),
        ),
        migrations.RunPython(
            backfill_content_guid, reverse_code=migrations.RunPython.noop
        ),
        migrations.RunPython(
            backfill_sha256, reverse_code=migrations.RunPython.noop
        ),
    ]
