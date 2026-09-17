"""Escopo de operações em lote sobre gates.

Um gate aplicado em várias amostras (`ApplyGateView`) gera cópias ligadas pela
FK `copied_from`. Operar "em todas as amostras do experimento" significa agir
sobre essa família de cópias — nunca sobre gates de outros experimentos, e
nunca sobre amostras desabilitadas.

`subsample` é o mesmo conjunto restrito às amostras do subsample da amostra
alvo; ele nunca amplia a família de cópias, apenas a recorta.
"""

from analytics.models import GateModel
from fcs_parser.models import FileDataModel

SCOPE_FILE = "file"
SCOPE_EXPERIMENT = "experiment"
SCOPE_SUBSAMPLE = "subsample"
SCOPE_CHOICES = [SCOPE_FILE, SCOPE_EXPERIMENT, SCOPE_SUBSAMPLE]
PROPAGATING_SCOPES = frozenset({SCOPE_EXPERIMENT, SCOPE_SUBSAMPLE})


def effective_scope(scope: str, file_data: FileDataModel) -> str:
    """Resolve `subsample` para `file` quando a amostra não está agrupada.

    Sem subsample não existe conjunto a propagar, e tratar isso como escopo de
    experimento seria ampliar o pedido do usuário.
    """
    if scope == SCOPE_SUBSAMPLE and file_data.subsample_id is None:
        return SCOPE_FILE
    return scope


def copy_family_ids(gate: GateModel) -> set[int]:
    """Ids do gate, do original que ele copiou e de todas as cópias derivadas."""
    root = gate
    seen_roots = {root.id}
    while root.copied_from_id and root.copied_from_id not in seen_roots:
        root = root.copied_from
        seen_roots.add(root.id)

    family = {root.id}
    frontier = [root.id]
    while frontier:
        children = list(
            GateModel.objects.filter(copied_from_id__in=frontier)
            .exclude(id__in=family)
            .values_list("id", flat=True)
        )
        if not children:
            break
        family.update(children)
        frontier = children

    return family


def gates_in_experiment_scope(
    gate: GateModel,
    target_file_data_ids: list[int] | None = None,
    include_source: bool = False,
    scope: str = SCOPE_EXPERIMENT,
):
    """Cópias do gate nas outras amostras (ativas) do mesmo experimento.

    Com `scope="subsample"` o conjunto fica restrito às amostras do subsample da
    amostra de origem.
    """
    queryset = GateModel.objects.filter(
        id__in=copy_family_ids(gate),
        file_data__experiment_id=gate.file_data.experiment_id,
        file_data__active=True,
        # A família de cópias nunca cruza branches (BE-23): `copied_from`
        # pode apontar para outra linha via fork, mas a propagação fica
        # restrita à branch do gate de origem.
        branch_id=gate.branch_id,
    )
    if scope == SCOPE_SUBSAMPLE:
        queryset = queryset.filter(file_data__subsample_id=gate.file_data.subsample_id)
    if not include_source:
        queryset = queryset.exclude(file_data_id=gate.file_data_id)
    if target_file_data_ids:
        allowed = set(target_file_data_ids)
        if include_source:
            allowed.add(gate.file_data_id)
        queryset = queryset.filter(file_data_id__in=allowed)
    return queryset
