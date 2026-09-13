from django.db import migrations


def backfill_source_path(apps, schema_editor):
    """Dados antigos não têm o caminho do ZIP; usa o nome do arquivo.

    Amostras homônimas no mesmo experimento (o caso que o `source_path` veio
    resolver) ficam com `source_path` vazio — a constraint ignora vazios. O ZIP
    continua no disco, então o caminho real é redescoberto fora da migração,
    pelo comando `manage.py repair_source_path`, que pode precisar recriar as
    amostras ambíguas.
    """
    FileDataModel = apps.get_model("fcs_parser", "FileDataModel")
    seen: set[tuple[int, str]] = set()

    for file_data in FileDataModel.objects.filter(source_path="").order_by("id"):
        name = file_data.file_name or ""
        if not name:
            continue
        key = (file_data.experiment_id, name)
        if key in seen:
            continue
        if (
            FileDataModel.objects.filter(
                experiment_id=file_data.experiment_id, file_name=name
            )
            .exclude(id=file_data.id)
            .exists()
        ):
            seen.add(key)
            continue
        seen.add(key)
        file_data.source_path = name
        file_data.save(update_fields=["source_path"])


class Migration(migrations.Migration):

    dependencies = [
        ("fcs_parser", "0010_filedatamodel_source_path_subsamplemodel_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_source_path, migrations.RunPython.noop),
    ]
