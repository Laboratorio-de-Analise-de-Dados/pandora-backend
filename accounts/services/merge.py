from django.apps import apps
from django.core import signing
from django.db import models, transaction
from django.utils import timezone

from accounts.models import AuthEvent, Membership, Role, SocialAccount, User
from accounts.services.oauth import log_auth_event

MERGE_TOKEN_SALT = "accounts.merge"
MERGE_TOKEN_MAX_AGE = 600  # 10 min para confirmar após o aviso de conflito


def make_merge_token(provider, provider_user_id, absorbed_user_id):
    """Token assinado que autoriza fundir `absorbed_user` na conta logada.

    Emitido quando o OAuth de link encontra a identidade já vinculada a
    outro usuário — a confirmação exige o usuário autenticado (a conta
    canônica é sempre a da sessão).
    """
    return signing.dumps(
        {
            "mode": "merge",
            "provider": provider,
            "sub": provider_user_id or "",
            "absorbed_id": absorbed_user_id,
        },
        salt=MERGE_TOKEN_SALT,
        compress=True,
    )


def read_merge_token(token):
    """Lê o token de merge; lança BadSignature/SignatureExpired."""
    payload = signing.loads(token, salt=MERGE_TOKEN_SALT, max_age=MERGE_TOKEN_MAX_AGE)
    if not isinstance(payload, dict) or payload.get("mode") != "merge":
        raise signing.BadSignature("token não é de merge")
    return payload


_ROLE_RANK = {Role.MEMBER: 0, Role.ORG_ADMIN: 1, Role.SUPER_ADMIN: 2}


def _role_rank(role):
    return _ROLE_RANK.get(role.name, -1)


def _merge_membership(canonical, absorbed_membership):
    """Mesma org nos dois lados: fica o papel mais forte; ativa se
    qualquer um estiver ativo. O vínculo absorvido é inativado
    (nunca deletado — preserva o rastro)."""
    existing = Membership.objects.filter(
        user=canonical, organization=absorbed_membership.organization
    ).first()
    if not existing:
        absorbed_membership.user = canonical
        absorbed_membership.save(update_fields=["user"])
        return

    stronger = max((existing.role, absorbed_membership.role), key=_role_rank)
    if existing.role != stronger or (
        existing.status != "active" and absorbed_membership.status == "active"
    ):
        existing.role = stronger
        if absorbed_membership.status == "active":
            existing.status = "active"
        existing.save(update_fields=["role", "status"])
    absorbed_membership.status = "inactive"
    absorbed_membership.save(update_fields=["status"])


def _migrate_user_fks(absorbed, canonical):
    """Move todas as FKs para User (autoria, ownership) do absorvido para
    o canônico — Membership e SocialAccount têm tratamento próprio e
    AuthEvent fica na conta absorvida (o evento pertence àquela identidade;
    o merge é registrado nos dois lados)."""
    skip = {Membership, SocialAccount, AuthEvent}
    for model in apps.get_models():
        if model in skip:
            continue
        for field in model._meta.get_fields():
            if (
                isinstance(field, models.ForeignKey)
                and field.related_model is User
                and field.name != "merged_into"
            ):
                model.objects.filter(**{field.name: absorbed}).update(
                    **{field.name: canonical}
                )


@transaction.atomic
def merge_accounts(request, canonical, absorbed):
    """Funde `absorbed` em `canonical`: memberships, autoria e vínculos de
    IdP migram; a conta absorvida fica inativa e rastreável (merged_into).
    Nada é deletado."""
    for membership in absorbed.memberships.all():
        _merge_membership(canonical, membership)

    SocialAccount.objects.filter(user=absorbed).update(user=canonical)
    _migrate_user_fks(absorbed, canonical)

    absorbed.is_active = False
    absorbed.merged_into = canonical
    absorbed.save(update_fields=["is_active", "merged_into"])

    summary = (
        f"Fundiu a conta {absorbed.email} ({absorbed.username}) "
        f"em {canonical.email}"
    )
    AuthEvent.objects.create(
        user=canonical,
        target_user=absorbed,
        action="merge",
        summary=summary,
        metadata={
            "absorbed_user_id": absorbed.id,
            "absorbed_email": absorbed.email,
            "ip": request.META.get("REMOTE_ADDR") if request else None,
            "user_agent": (
                request.META.get("HTTP_USER_AGENT", "")[:200] if request else ""
            ),
        },
    )
    log_auth_event(
        request,
        absorbed,
        "merge",
        summary=f"Conta fundida em {canonical.email}",
        canonical_user_id=canonical.id,
    )
    return canonical
