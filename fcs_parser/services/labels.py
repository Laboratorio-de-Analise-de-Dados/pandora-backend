"""BE-34: mecânica de labels semânticas por amostra.

``set_file_labels`` é o **único caminho de escrita** de vínculos
amostra ↔ label — é aqui que a exclusividade de controle (no máximo
uma label de ``category="control"`` por amostra) e a visibilidade de
escopo são garantidas.

``suggest_labels`` é a heurística de sugestão por nome de arquivo:
informativa, nunca aplicada sozinha — o usuário confirma.
"""

from __future__ import annotations

import re

from django.db import transaction
from django.db.models import Q
from rest_framework import serializers

from fcs_parser.models import FileLabelModel, SampleLabelModel


def labels_visible_to(user):
    """Vocabulário visível: sistema + organizações do usuário + pessoais dele."""
    qs = SampleLabelModel.objects.filter(active=True)
    if not getattr(user, "is_authenticated", False):
        return qs.filter(scope=SampleLabelModel.SCOPE_SYSTEM)
    org_ids = user.memberships.filter(status="active").values_list(
        "organization_id", flat=True
    )
    return qs.filter(
        Q(scope=SampleLabelModel.SCOPE_SYSTEM)
        | Q(
            scope=SampleLabelModel.SCOPE_ORGANIZATION,
            organization_id__in=org_ids,
        )
        | Q(scope=SampleLabelModel.SCOPE_PERSONAL, created_by=user)
    )


def can_edit_label(user, label: SampleLabelModel) -> bool:
    """Quem pode renomear/recolorir uma label de usuário.

    Labels de sistema nunca são editáveis pela API. Org labels são
    editáveis por qualquer membro ativo da organização; pessoais, só
    pelo criador. Super admin edita qualquer uma de usuário.
    """
    if label.scope == SampleLabelModel.SCOPE_SYSTEM:
        return False
    if getattr(user, "is_super_admin", False):
        return True
    if label.scope == SampleLabelModel.SCOPE_ORGANIZATION:
        return user.memberships.filter(
            organization_id=label.organization_id, status="active"
        ).exists()
    return label.created_by_id == user.id


def set_file_labels(file_data, label_ids, user) -> list[SampleLabelModel]:
    """Define o conjunto de labels de uma amostra (substituição completa).

    Valida que todas as labels são visíveis ao usuário e que há no
    máximo uma de ``category="control"``. Retorna as labels aplicadas.
    """
    unique_ids = list(dict.fromkeys(label_ids or []))
    labels = list(labels_visible_to(user).filter(id__in=unique_ids))
    if len(labels) != len(unique_ids):
        raise serializers.ValidationError({"labels": "Label inválida ou inacessível."})
    if sum(1 for label in labels if label.category == "control") > 1:
        raise serializers.ValidationError(
            {"labels": "Uma amostra só pode ter um tipo de controle."}
        )

    with transaction.atomic():
        FileLabelModel.objects.filter(file_data=file_data).exclude(
            label__in=labels
        ).delete()
        existing = set(
            FileLabelModel.objects.filter(file_data=file_data).values_list(
                "label_id", flat=True
            )
        )
        FileLabelModel.objects.bulk_create(
            [
                FileLabelModel(file_data=file_data, label=label, created_by=user)
                for label in labels
                if label.id not in existing
            ]
        )
    return labels


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


def suggest_labels(file_name) -> list[str]:
    """Sugere `system_key` provável pelo nome do arquivo.

    Retorna lista com 0 ou 1 chave — informativa para a UI exibir como
    "sugerido"; nunca é aplicada sem confirmação do usuário.
    """
    name = (file_name or "").lower()
    for pattern, key in _SUGGESTION_RULES:
        if pattern.search(name):
            return [key]
    return []
