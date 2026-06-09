import django.db.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0006_alter_analysisresult_gate"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="gatemodel",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="gatemodel",
            constraint=models.UniqueConstraint(
                fields=["name", "parent"],
                name="unique_gate_name_per_parent",
            ),
        ),
        migrations.AddConstraint(
            model_name="gatemodel",
            constraint=models.UniqueConstraint(
                condition=models.Q(parent__isnull=True),
                fields=["name", "file_data"],
                name="unique_gate_name_root_level",
            ),
        ),
    ]
