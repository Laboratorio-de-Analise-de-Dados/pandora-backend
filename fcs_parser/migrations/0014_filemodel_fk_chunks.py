from django.contrib.postgres.fields import ArrayField
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("fcs_parser", "0013_content_sha256"),
    ]

    operations = [
        migrations.AlterField(
            model_name="filemodel",
            name="experiment",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="uploads",
                to="fcs_parser.experimentmodel",
            ),
        ),
        migrations.AddField(
            model_name="filemodel",
            name="total_chunks",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="filemodel",
            name="received_chunks",
            field=ArrayField(
                base_field=models.IntegerField(), blank=True, default=list
            ),
        ),
    ]
