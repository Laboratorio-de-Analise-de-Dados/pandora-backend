# Generated manually for MVP user/org simplification

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_remove_user_organization_remove_user_role_and_more"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="membership",
            unique_together={("user", "organization")},
        ),
        migrations.AlterField(
            model_name="membership",
            name="organization",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="memberships",
                to="accounts.organization",
            ),
        ),
        migrations.AlterField(
            model_name="membership",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="memberships",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="role",
            name="name",
            field=models.CharField(
                choices=[
                    ("super_admin", "Super Admin"),
                    ("org_admin", "Org Admin"),
                    ("member", "Member"),
                ],
                max_length=50,
                unique=True,
            ),
        ),
    ]
