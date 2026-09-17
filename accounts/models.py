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


class SocialAccount(models.Model):
    """Vínculo entre o usuário e uma identidade de provider externo (IdP).

    `provider_user_id` é o identificador imutável do provider (`sub`/`oid`);
    `email` é informativo — o provider pode mudar o email sem quebrar o
    vínculo. Nunca é deletado: desvincular marca `active=False`.
    """

    PROVIDERS = [
        ("google", "Google"),
        ("microsoft", "Microsoft"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="social_accounts"
    )
    provider = models.CharField(max_length=20, choices=PROVIDERS)
    provider_user_id = models.CharField(max_length=255)
    email = models.EmailField()
    active = models.BooleanField(default=True)
    linked_at = models.DateTimeField(auto_now_add=True)
    unlinked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_user_id"],
                name="unique_provider_identity",
            )
        ]
        indexes = [
            models.Index(fields=["user", "active"], name="socialacct_user_active_idx")
        ]

    def __str__(self):
        return f"{self.provider}:{self.email} → {self.user.username}"


class AuthEvent(models.Model):
    """Log append-only de eventos de autenticação (LGPD/auditoria).

    Mesmo espírito do histórico de análise: só INSERT, `summary` pronto
    para exibição. Nunca editar nem deletar linhas.
    """

    ACTIONS = [
        ("login_sso", "Login via SSO"),
        ("login_local", "Login com senha"),
        ("link", "Vínculo de identidade"),
        ("unlink", "Desvínculo de identidade"),
        ("merge", "Merge de contas"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="auth_events",
    )
    action = models.CharField(max_length=20, choices=ACTIONS)
    provider = models.CharField(max_length=20, blank=True, default="")
    target_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Conta absorvida em merges (BE-30).",
    )
    metadata = models.JSONField(default=dict, blank=True)
    summary = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="authevent_user_ts_idx")
        ]

    def __str__(self):
        return f"{self.action} - {self.user_id} ({self.created_at:%Y-%m-%d %H:%M})"


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
