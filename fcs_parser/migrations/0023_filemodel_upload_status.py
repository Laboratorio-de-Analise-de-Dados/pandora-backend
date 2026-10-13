# BE-42: estado por upload (FileModel) — erro por arquivo, não por experimento.

from django.db import migrations, models


def backfill_upload_status(apps, schema_editor):
    """Uploads históricos: com `file` gravado são "done"; reservas sem
    arquivo nunca concluídas ficam "uploading" (estado neutro)."""
    FileModel = apps.get_model("fcs_parser", "FileModel")
    FileModel.objects.exclude(file__isnull=True).exclude(file="").update(status="done")


class Migration(migrations.Migration):

    dependencies = [
        ("fcs_parser", "0022_relative_media_paths"),
    ]

    operations = [
        migrations.AddField(
            model_name="filemodel",
            name="status",
            field=models.CharField(
                choices=[
                    ("uploading", "Uploading"),
                    ("done", "Done"),
                    ("error", "Error"),
                ],
                default="uploading",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="filemodel",
            name="error_info",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(
            backfill_upload_status, reverse_code=migrations.RunPython.noop
        ),
    ]
