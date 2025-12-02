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
    

    def __str__(self):
        return self.username


class Role(models.Model):
    ROLE_CHOICES = [
        ("Admin", "Admin"),
        ("Editor", "Editor"),
        ("Viewer", "Viewer"),
    ]

    name = models.CharField(
        max_length=50,
        choices=ROLE_CHOICES,
        unique=True
    )
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
    status = models.CharField(
        max_length=20,
        choices=[("active", "Active"), ("inactive", "Inactive"), ("pending", "Pending")],
        default="pending"
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
        choices=[("pending", "Pending"), ("accepted", "Accepted"), ("expired", "Expired")],
        default="pending"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
