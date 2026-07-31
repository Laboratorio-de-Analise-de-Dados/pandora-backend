from django.contrib.auth.models import AbstractUser, Permission
from django.db import models


class Organization(models.Model):
    """
    Representa uma organização (laboratório, cliente, etc.).
    """

    name = models.CharField(max_length=255)
    org_type = models.CharField(
        max_length=50,
        choices=[("lab", "Laboratório"), ("customer", "Cliente")],
        default="customer",
    )
    external_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="ID externo para integração futura (OIDC group, LDAP, etc.)",
    )

    def __str__(self):
        return f"{self.name} ({self.org_type})"


class User(AbstractUser):
    """
    Usuário customizado estendendo AbstractUser.
    """

    AUTH_PROVIDERS = [
        ("local", "Local"),
        ("google", "Google"),
        ("microsoft", "Microsoft"),
    ]

    auth_provider = models.CharField(
        max_length=20,
        choices=AUTH_PROVIDERS,
        default="local",
        help_text="Provedor usado para autenticação deste usuário.",
    )

    def __str__(self):
        return self.username

    @property
    def is_super_admin(self):
        return self.is_superuser or self.is_staff


class Role(models.Model):
    """
    Papéis globais e simples usados nos Memberships.
    """

    SUPER_ADMIN = "super_admin"
    ORG_ADMIN = "org_admin"
    MEMBER = "member"

    ROLE_CHOICES = [
        (SUPER_ADMIN, "Super Admin"),
        (ORG_ADMIN, "Org Admin"),
        (MEMBER, "Member"),
    ]

    name = models.CharField(max_length=50, choices=ROLE_CHOICES, unique=True)
    permissions = models.ManyToManyField(
        Permission,
        blank=True,
        help_text="Permissões ligadas ao papel (auth.Permission).",
    )

    def __str__(self):
        return self.name


class Membership(models.Model):
    """
    Relação N:N entre User e Organization com um Role.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    status = models.CharField(
        max_length=20,
        choices=[
            ("active", "Active"),
            ("inactive", "Inactive"),
            ("pending", "Pending"),
        ],
        default="pending",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "organization")

    def __str__(self):
        return f"{self.user.username} - {self.organization.name} ({self.role.name})"


class Invite(models.Model):
    email = models.EmailField()
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("pending", "Pending"),
            ("accepted", "Accepted"),
            ("expired", "Expired"),
        ],
        default="pending",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
