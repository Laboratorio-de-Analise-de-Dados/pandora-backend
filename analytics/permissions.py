"""Escopo de acesso a gates e objetos de análise.

Mesma regra de ``fcs_parser.permissions``: o gate é alcançável quando a
amostra dele pertence a um experimento visível ao usuário (ADR-0014).
"""

from rest_framework.exceptions import PermissionDenied

from fcs_parser.permissions import can_edit_experiment, experiments_visible_to
from analytics.models import GateModel


def gates_visible_to(user):
    """Gates alcançáveis: a amostra pertence a experimento visível e ativo."""
    return GateModel.objects.filter(
        file_data__experiment__in=experiments_visible_to(user)
    )


def can_edit_gate(user, gate) -> bool:
    """Escrita no gate exige can_edit_experiment na amostra dona dele."""
    file_data = getattr(gate, "file_data", None)
    if file_data is None:
        return user.is_super_admin
    return can_edit_experiment(user, file_data.experiment)


def require_can_edit_gate(user, gate):
    """Levanta PermissionDenied (403) quando ``can_edit_gate`` falha."""
    if not can_edit_gate(user, gate):
        raise PermissionDenied("Você não tem permissão para alterar este experimento.")
