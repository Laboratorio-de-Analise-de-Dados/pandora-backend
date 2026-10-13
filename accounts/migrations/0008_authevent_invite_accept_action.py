# BE-41: AuthEvent registra aceite de convite (inclusive com e-mail divergente).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_user_merged_into"),
    ]

    operations = [
        migrations.AlterField(
            model_name="authevent",
            name="action",
            field=models.CharField(
                choices=[
                    ("login_sso", "Login via SSO"),
                    ("login_local", "Login com senha"),
                    ("link", "Vínculo de identidade"),
                    ("unlink", "Desvínculo de identidade"),
                    ("merge", "Merge de contas"),
                    ("invite_accept", "Aceite de convite"),
                ],
                max_length=20,
            ),
        ),
    ]
