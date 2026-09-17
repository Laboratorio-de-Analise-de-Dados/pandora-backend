import re

import jwt
import requests
from django.conf import settings
from django.core import signing
from django.utils import timezone


def decode_claims(id_token):
    """Extrai claims do id_token sem verificar assinatura.

    A assinatura já foi validada implicitamente: o token veio direto do
    endpoint de token do provider em resposta HTTPS ao nosso code+secret.
    Os claims são usados para identidade (sub/oid, email), não para
    autorização sensível.
    """
    if not id_token:
        return {}
    try:
        return jwt.decode(id_token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return {}


def microsoft_fetch_identity(code):
    """Troca o code por tokens e busca o perfil no Graph.

    Retorna {"sub", "email", "name"} ou lança requests.RequestException.
    """
    tenant = settings.MICROSOFT_TENANT_ID or "common"
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    token_response = requests.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "client_id": settings.MICROSOFT_CLIENT_ID,
            "client_secret": settings.MICROSOFT_CLIENT_SECRET,
            "code": code,
            "redirect_uri": settings.MICROSOFT_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    token_response.raise_for_status()
    tokens = token_response.json()

    claims = decode_claims(tokens.get("id_token"))
    graph_response = requests.get(
        "https://graph.microsoft.com/v1.0/me",
        params={"$select": "id,displayName,mail,userPrincipalName,otherMails"},
        headers={"Authorization": f"Bearer {tokens.get('access_token')}"},
    )
    graph_response.raise_for_status()
    profile = graph_response.json()

    email = resolve_microsoft_email(profile, claims)
    return {
        "sub": claims.get("oid") or claims.get("sub") or profile.get("id"),
        "email": email,
        "name": profile.get("displayName") or (email.split("@")[0] if email else ""),
    }


def google_fetch_identity(code):
    """Troca o code por tokens e busca o perfil no userinfo.

    Retorna {"sub", "email", "name"} ou lança requests.RequestException.
    """
    token_response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "authorization_code",
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "code": code,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    token_response.raise_for_status()
    tokens = token_response.json()

    claims = decode_claims(tokens.get("id_token"))
    userinfo_response = requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo",
        params={"alt": "json"},
        headers={"Authorization": f"Bearer {tokens.get('access_token')}"},
    )
    userinfo_response.raise_for_status()
    profile = userinfo_response.json()

    email = profile.get("email") or claims.get("email")
    return {
        "sub": claims.get("sub") or profile.get("id"),
        "email": email,
        "name": profile.get("name") or (email.split("@")[0] if email else ""),
    }


LINK_STATE_SALT = "accounts.oauth-link-state"
LINK_STATE_MAX_AGE = 600  # 10 min para completar o OAuth em modo link


def make_link_state(user_id):
    """State assinado para o OAuth em modo vínculo (perfil).

    Carrega o user_id dentro do próprio state — não depende de cookie de
    sessão, que seria bloqueado no fluxo cross-origin do front.
    """
    import secrets as _secrets

    return signing.dumps(
        {"mode": "link", "user_id": user_id, "nonce": _secrets.token_urlsafe(16)},
        salt=LINK_STATE_SALT,
    )


def read_link_state(state):
    """Retorna o payload se o state for um link-state válido, senão None."""
    if not state:
        return None
    try:
        payload = signing.loads(state, salt=LINK_STATE_SALT, max_age=LINK_STATE_MAX_AGE)
    except signing.BadSignature:
        return None
    if isinstance(payload, dict) and payload.get("mode") == "link":
        return payload
    return None


LINK_TOKEN_SALT = "accounts.social-link"
LINK_TOKEN_MAX_AGE = 600  # 10 min entre o aviso e a confirmação


def make_link_token(provider, provider_user_id, email, user_id):
    """Token assinado (não-criptografado) para confirmar vínculo no login."""
    return signing.dumps(
        {
            "provider": provider,
            "sub": provider_user_id or "",
            "email": email,
            "user_id": user_id,
        },
        salt=LINK_TOKEN_SALT,
        compress=True,
    )


def read_link_token(token):
    """Valida e lê o token de vínculo; lança BadSignature/SignatureExpired."""
    return signing.loads(token, salt=LINK_TOKEN_SALT, max_age=LINK_TOKEN_MAX_AGE)


def log_auth_event(request, user, action, provider="", summary="", **extra_meta):
    """Registra evento de autenticação (append-only) com contexto da request."""
    from accounts.models import AuthEvent

    metadata = {
        "ip": request.META.get("REMOTE_ADDR") if request else None,
        "user_agent": request.META.get("HTTP_USER_AGENT", "")[:200] if request else "",
        **extra_meta,
    }
    return AuthEvent.objects.create(
        user=user,
        action=action,
        provider=provider,
        metadata=metadata,
        summary=summary,
    )


def unlink_social_account(request, account):
    """Desvincula preservando o histórico (soft unlink + AuthEvent)."""
    account.active = False
    account.unlinked_at = timezone.now()
    account.save(update_fields=["active", "unlinked_at"])
    log_auth_event(
        request,
        account.user,
        "unlink",
        provider=account.provider,
        summary=f"Desvinculou {account.provider} ({account.email})",
        provider_email=account.email,
    )


def unique_username_for_email(email):
    """Gera username único a partir do local part do email.

    Sanitiza para o charset aceito pelo validator do Django
    (\\w, @, ., +, -, _) e acrescenta sufixo numérico em colisão —
    dois usuários "paulo@..." de domínios diferentes não podem
    quebrar o login do segundo.
    """
    from accounts.models import User

    base = re.sub(r"[^\w.@+-]", "_", email.split("@")[0]).strip("_") or "user"
    base = base[:140]
    username = base
    suffix = 2
    while User.objects.filter(username=username).exists():
        username = f"{base}{suffix}"
        suffix += 1
    return username


def _normalize_microsoft_address(address):
    if not address:
        return None
    if "#EXT#" in address:
        local = address.split("#EXT#", 1)[0]
        local, _, domain = local.rpartition("_")
        if local and domain:
            address = f"{local}@{domain}"
    return address or None


def resolve_microsoft_email(profile, claims):
    """Resolve o email real do usuário a partir do perfil Graph + id_token.

    Contas organizacionais sem `mail` populado (ex.: alunos Fiocruz) chegam
    com userPrincipalName `<user>@<tenant>.onmicrosoft.com`, e contas
    pessoais convidadas vêm no formato `<user>_<domain>#EXT#@tenant`.
    Preferimos qualquer endereço que não seja onmicrosoft/EXT#; o UPN fica
    como último fallback para nunca quebrar o login.
    """
    candidates = [
        profile.get("mail"),
        *(profile.get("otherMails") or []),
        claims.get("email"),
        claims.get("preferred_username"),
        profile.get("userPrincipalName"),
    ]
    normalized = [addr for c in candidates if (addr := _normalize_microsoft_address(c))]
    for addr in normalized:
        if not addr.lower().endswith(".onmicrosoft.com"):
            return addr
    return normalized[0] if normalized else None
