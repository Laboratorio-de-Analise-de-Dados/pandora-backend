from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0009_add_color_to_gate"),
    ]

    operations = [
        migrations.AddField(
            model_name="gatemodel",
            name="plot_config",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
