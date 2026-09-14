"""Nome de exibição do autor do gate, compartilhado entre serializers e build_tree."""


def author_display_name(first_name, last_name, username):
    """Nome completo do autor, com fallback para username; None sem autor."""
    if not username:
        return None
    full_name = f"{first_name or ''} {last_name or ''}".strip()
    return full_name or username
