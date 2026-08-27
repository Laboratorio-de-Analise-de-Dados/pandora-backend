from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fcs_parser", "0006_experimentmodel_zip_path"),
    ]

    operations = [
        migrations.AlterField(
            model_name="experimentmodel",
            name="title",
            field=models.CharField(max_length=50),
        ),
        migrations.AlterUniqueTogether(
            name="experimentmodel",
            unique_together={("title", "organization")},
        ),
    ]
