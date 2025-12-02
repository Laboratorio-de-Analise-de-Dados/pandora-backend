from django.contrib.auth.models import AbstractUser, Permission
from django.db import models


class Organization(models.Model):
    """
    Representa uma organização genérica (laboratório, cliente, parceiro, etc.)
    """
    name = models.CharField(max_length=255)
    org_type = models.CharField(
        max_length=50,
        choices=[("lab", "Laboratório"), ("customer", "Cliente")],
        default="customer"
    )
    external_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="ID externo para integração futura (OIDC group, LDAP, etc.)"
    )

    def __str__(self):
        return f"{self.name} ({self.org_type})"


class User(AbstractUser):
    """
    Usuário customizado, estendendo AbstractUser.
    Mantém vínculo simples com uma organização principal e um role básico.
    """
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users"
    )
    role = models.CharField(
        max_length=50,
        default="member",
        help_text="Função simples (admin, member, etc.)."
    )

    def __str__(self):
        return self.username


class Role(models.Model):
    """
    Define papéis (roles) independentes, que podem ter permissões associadas.
    Exemplo: Admin, Editor, Viewer.
    """
    name = models.CharField(max_length=50, unique=True)
    permissions = models.ManyToManyField(
        Permission,
        blank=True,
        help_text="Permissões ligadas ao papel (auth.Permission)."
    )

    def __str__(self):
        return self.name


class Membership(models.Model):
    """
    Relação n:n entre User e Organization, com um Role específico.
    Permite que um usuário tenha múltiplos papéis em múltiplas organizações.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("user", "organization")

    def __str__(self):
        return f"{self.user.username} - {self.organization.name} ({self.role.name})"