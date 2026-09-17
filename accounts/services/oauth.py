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
