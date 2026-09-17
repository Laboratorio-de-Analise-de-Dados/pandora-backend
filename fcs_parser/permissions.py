"""Escopo de acesso a experimentos.

Centraliza a regra "quais experimentos este usuário alcança" para que views de
experimento, de arquivo e de gate compartilhem o mesmo critério.

Todo lookup por id passa por um queryset escopado daqui (ADR-0014):
``get_object_or_404(file_data_visible_to(user), id=...)`` devolve 404 para
objeto fora do escopo em vez de vazar a existência dele.
"""

from rest_framework.exceptions import PermissionDenied

from fcs_parser.models import ExperimentModel, FileDataModel, FileModel


def experiments_visible_to(user, include_inactive=False):
    """Experimentos que o usuário pode ler.

    Inativos ficam fora por default (ADR-0005); ``include_inactive=True``
    implementa o ``?include_inactive=true`` das listagens.
    """
    qs = ExperimentModel.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    if user.is_super_admin:
        return qs

    org_ids = user.memberships.filter(status="active").values_list(
        "organization_id", flat=True
    )
    return qs.filter(organization_id__in=org_ids) | qs.filter(created_by=user)


def file_data_visible_to(user):
    """Amostras alcançáveis: o experimento precisa ser visível e ativo.

    O ``active`` da própria amostra NÃO é filtrado aqui — cada endpoint decide
    (listagens filtram, endpoints de escrita precisam achar inativas).
    """
    return FileDataModel.objects.filter(experiment__in=experiments_visible_to(user))


def uploads_visible_to(user):
    """Uploads (FileModel) alcançáveis pelo usuário."""
    return FileModel.objects.filter(experiment__in=experiments_visible_to(user))


def can_edit_experiment(user, experiment) -> bool:
    """Escrita exige ser o criador, membro ativo da organização ou super admin."""
    if user.is_super_admin or experiment.created_by_id == user.id:
        return True
    if experiment.organization_id is None:
        return False
    return user.memberships.filter(
        organization_id=experiment.organization_id, status="active"
    ).exists()


def is_org_member(user, organization_id: int) -> bool:
    """Membro ativo da organização (ou super admin)."""
    if user.is_super_admin:
        return True
    return user.memberships.filter(
        organization_id=organization_id, status="active"
    ).exists()


def can_move_experiment(user, experiment) -> bool:
    """Mover muda quem vê o experimento — exige dono ou admin na origem.

    Dono = ``created_by``; admin = super admin ou membership ativa com papel
    ``org_admin`` na organização de origem.
    """
    if user.is_super_admin or experiment.created_by_id == user.id:
        return True
    if experiment.organization_id is None:
        return False
    return user.memberships.filter(
        organization_id=experiment.organization_id,
        status="active",
        role__name="org_admin",
    ).exists()


def can_create_experiment_type(user, experiment=None) -> bool:
    """Criar entrada nova no vocabulário de tipos (BE-28/ADR-0023).

    "Admin" aqui cobre os dois níveis: ``is_super_admin`` sempre pode; e
    ``org_admin`` — admin da organização — cura o vocabulário do próprio
    lab: no endpoint solto basta ser org_admin ativo de alguma org, e com
    experimento precisa ser org_admin da org dele. Fora isso, só o dono
    (``created_by``) — quem cria o próprio experimento é dono por
    definição. Membro comum editando experimento alheio só escolhe entre
    os tipos existentes.
    """
    if user.is_super_admin:
        return True
    if experiment is None:
        return user.memberships.filter(status="active", role__name="org_admin").exists()
    if experiment.created_by_id == user.id:
        return True
    if experiment.organization_id is None:
        return False
    return user.memberships.filter(
        organization_id=experiment.organization_id,
        status="active",
        role__name="org_admin",
    ).exists()


def require_can_edit_experiment(user, experiment):
    """Levanta PermissionDenied (403) quando ``can_edit_experiment`` falha."""
    if not can_edit_experiment(user, experiment):
        raise PermissionDenied("Você não tem permissão para alterar este experimento.")


def require_can_edit_file_data(user, file_data):
    """Mesma regra, atalho para quem já tem a amostra na mão."""
    require_can_edit_experiment(user, file_data.experiment)
