"""BE-34: mecânica de tags semânticas por amostra.

``set_file_tags`` é o **único caminho de escrita** de vínculos
amostra ↔ tag — é aqui que a exclusividade de controle (no máximo
uma tag de ``category="control"`` por amostra) e a visibilidade de
escopo são garantidas.

``suggest_tags`` é a heurística de sugestão por nome de arquivo:
informativa, nunca aplicada sozinha — o usuário confirma.
"""

from __future__ import annotations

import re

from django.db import transaction
from django.db.models import Q
from rest_framework import serializers

from fcs_parser.models import FileTagModel, SampleTagModel


def tags_visible_to(user):
    """Vocabulário visível: sistema + organizações do usuário + pessoais dele."""
    qs = SampleTagModel.objects.filter(active=True)
    if not getattr(user, "is_authenticated", False):
        return qs.filter(scope=SampleTagModel.SCOPE_SYSTEM)
    org_ids = user.memberships.filter(status="active").values_list(
        "organization_id", flat=True
    )
    return qs.filter(
        Q(scope=SampleTagModel.SCOPE_SYSTEM)
        | Q(
            scope=SampleTagModel.SCOPE_ORGANIZATION,
            organization_id__in=org_ids,
        )
        | Q(scope=SampleTagModel.SCOPE_PERSONAL, created_by=user)
    )


def can_edit_tag(user, tag: SampleTagModel) -> bool:
    """Quem pode renomear/recolorir uma tag de usuário.

    Tags de sistema nunca são editáveis pela API. Org tags são
    editáveis por qualquer membro ativo da organização; pessoais, só
    pelo criador. Super admin edita qualquer uma de usuário.
    """
    if tag.scope == SampleTagModel.SCOPE_SYSTEM:
        return False
    if getattr(user, "is_super_admin", False):
        return True
    if tag.scope == SampleTagModel.SCOPE_ORGANIZATION:
        return user.memberships.filter(
            organization_id=tag.organization_id, status="active"
        ).exists()
    return tag.created_by_id == user.id


def resolve_tags(tag_ids, user, allow_control=True) -> list[SampleTagModel]:
    """Resolve ids em tags visíveis, aplicando as regras do domínio.

    - todos os ids precisam existir e ser visíveis ao usuário;
    - no máximo uma tag de ``category="control"`` por conjunto;
    - ``allow_control=False`` (tags de subsample) recusa controle:
      o papel de controle do grupo é ``control_type``, não tag.
    """
    unique_ids = list(dict.fromkeys(tag_ids or []))
    tags = list(tags_visible_to(user).filter(id__in=unique_ids))
    if len(tags) != len(unique_ids):
        raise serializers.ValidationError({"tags": "Tag inválida ou inacessível."})
    control_count = sum(1 for tag in tags if tag.category == "control")
    if not allow_control and control_count:
        raise serializers.ValidationError(
            {"tags": "Controle de subsample vai em control_type, não em tag."}
        )
    if control_count > 1:
        raise serializers.ValidationError(
            {"tags": "Uma amostra só pode ter um tipo de controle."}
        )
    return tags


def set_file_tags(file_data, tag_ids, user) -> list[SampleTagModel]:
    """Define o conjunto de tags de uma amostra (substituição completa).

    Valida que todas as tags são visíveis ao usuário e que há no
    máximo uma de ``category="control"``. Retorna as tags aplicadas.
    """
    tags = resolve_tags(tag_ids, user)

    with transaction.atomic():
        FileTagModel.objects.filter(file_data=file_data).exclude(tag__in=tags).delete()
        existing = set(
            FileTagModel.objects.filter(file_data=file_data).values_list(
                "tag_id", flat=True
            )
        )
        FileTagModel.objects.bulk_create(
            [
                FileTagModel(file_data=file_data, tag=tag, created_by=user)
                for tag in tags
                if tag.id not in existing
            ]
        )
    return tags


def set_subsample_tags(subsample, tag_ids, user) -> list[SampleTagModel]:
    """Define as tags de contexto de um subsample (substituição completa).

    Tags de grupo são só contexto — a exclusividade de controle nem se
    aplica porque ``resolve_tags`` recusa ``category="control"`` aqui.
    """
    tags = resolve_tags(tag_ids, user, allow_control=False)
    subsample.tags.set(tags)
    return tags


# ``control_type`` do subsample → system_key da tag de controle herdada.
# O grupo é homogêneo (BE-22): todos os membros dividem o mesmo papel.
CONTROL_TYPE_TO_SYSTEM_KEY = {
    "unstained": "unstained",
    "single_stain": "single_stain",
}


def inherited_tags(file_data) -> list[SampleTagModel]:
    """Tags herdadas do subsample da amostra — herança virtual.

    Nada é copiado: o conjunto efetivo é calculado na leitura. Regras:

    - o ``control_type`` do grupo vira a tag de sistema correspondente;
    - tags de contexto do grupo entram inteiras;
    - **tag explícita vence**: se a amostra já tem tag de controle
      própria, o controle herdado não entra (override por arquivo —
      "esse arquivo do grupo na verdade é FMO"); ids já próprios não
      duplicam.
    """
    subsample = getattr(file_data, "subsample", None)
    if subsample is None or not subsample.active:
        return []

    own = file_data.tags.filter(active=True)
    own_ids = {tag.id for tag in own}
    own_has_control = any(tag.category == "control" for tag in own)

    result = []
    if subsample.control_type and not own_has_control:
        system_key = CONTROL_TYPE_TO_SYSTEM_KEY.get(subsample.control_type)
        control_tag = SampleTagModel.objects.filter(
            system_key=system_key, active=True
        ).first()
        if control_tag and control_tag.id not in own_ids:
            result.append(control_tag)
    for tag in subsample.tags.filter(active=True):
        if tag.id not in own_ids:
            result.append(tag)
    return result


# Regras de sugestão por filename — (padrão regex, system_key). A
# primeira regra que casa vence: a exclusividade de controle vale até
# para sugestão (sugerir dois controles confundiria a confirmação).
# Fronteira de token é lookaround, não \b: filenames FCS usam "_" como
# separador ("fmo_cd4", "sample_ctrl_01") e "_" é word char para \b.
_TOK_L = r"(?<![a-z0-9])"
_TOK_R = r"(?![a-z0-9])"
_SUGGESTION_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"unstain|{_TOK_L}neg(ative)?{_TOK_R}"), "unstained"),
    (re.compile(rf"{_TOK_L}fmo{_TOK_R}"), "fmo"),
    (re.compile(r"isotyp|iso[_-]?type"), "isotype"),
    (
        re.compile(rf"single[_-]?stain|{_TOK_L}ss[_-]?\d|{_TOK_L}comp{_TOK_R}"),
        "single_stain",
    ),
    (re.compile(rf"{_TOK_L}beads?{_TOK_R}"), "beads"),
    (re.compile(rf"{_TOK_L}ref(erence)?{_TOK_R}|{_TOK_L}biolog"), "biological_ref"),
    # "control"/"ctrl" genérico por último: melhor palpite é o negativo,
    # mas só se nenhum padrão mais informativo casou antes.
    (re.compile(rf"{_TOK_L}controls?{_TOK_R}|{_TOK_L}ctrl{_TOK_R}"), "unstained"),
]


def suggest_tags(file_name) -> list[str]:
    """Sugere `system_key` provável pelo nome do arquivo.

    Retorna lista com 0 ou 1 chave — informativa para a UI exibir como
    "sugerido"; nunca é aplicada sem confirmação do usuário.
    """
    name = (file_name or "").lower()
    for pattern, key in _SUGGESTION_RULES:
        if pattern.search(name):
            return [key]
    return []
