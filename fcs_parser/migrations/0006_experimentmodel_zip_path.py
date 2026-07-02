from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "fcs_parser",
            "0005_filedatamodel_fcs_path_filedatamodel_last_accessed_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="experimentmodel",
            name="zip_path",
            field=models.CharField(blank=True, max_length=512, null=True),
        ),
    ]
