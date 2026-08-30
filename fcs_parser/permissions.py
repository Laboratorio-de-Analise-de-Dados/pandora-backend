"""Escopo de acesso a experimentos.

Centraliza a regra "quais experimentos este usuário alcança" para que views de
experimento, de arquivo e de gate compartilhem o mesmo critério.
"""

from fcs_parser.models import ExperimentModel


def experiments_visible_to(user):
    """Experimentos que o usuário pode ler."""
    if user.is_super_admin:
        return ExperimentModel.objects.all()

    org_ids = user.memberships.filter(status="active").values_list(
        "organization_id", flat=True
    )
    return ExperimentModel.objects.filter(
        organization_id__in=org_ids
    ) | ExperimentModel.objects.filter(created_by=user)


def can_edit_experiment(user, experiment) -> bool:
    """Escrita exige ser o criador, membro ativo da organização ou super admin."""
    if user.is_super_admin or experiment.created_by_id == user.id:
        return True
    if experiment.organization_id is None:
        return False
    return user.memberships.filter(
        organization_id=experiment.organization_id, status="active"
    ).exists()
