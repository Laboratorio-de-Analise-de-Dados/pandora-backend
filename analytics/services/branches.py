"""Branches de análise — BE-23, ADR-0020.

Branch = cópia materializada das árvores de gates: o fork cria linhas
reais na branch nova (nunca replay de revisões). A identidade entre
branches usa ``GateModel.forked_from`` — ``copied_from`` continua
reservado à família de cópias intra-branch (ADR-0003), que edições de
geometria podem desfazer; ``forked_from`` sobrevive a elas e ancora o
merge.

O merge é diff estrutural ``source → target`` (v1: só filha → base —
``target`` precisa ser o ``base_branch`` da origem). O ``fork_snapshot``
guarda o estado da base no fork: {"pairs": {b_id: a_id}, "base":
{a_id: campos}} — permite distinguir "editado só na filha" de "editado
nos dois lados" e "deletado na base" mesmo com ``forked_from`` SET_NULL.
"""

from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404

from analytics.history import (
    gate_snapshot,
    gate_subtree_snapshots,
    record_revision,
)
from analytics.models import (
    AnalysisBranch,
    AnalysisResult,
    AnalysisRevision,
    GateModel,
)
from fcs_parser.models import FileDataModel

MAIN_BRANCH_NAME = "main"

# Campos que o merge compara/propaga (estrutura/parent não mudam por edição).
MERGE_FIELDS = ("name", "gate_coordinates", "plot_config", "color")


def ensure_main_branch(experiment) -> AnalysisBranch:
    """A branch vigente do experimento — criada sob demanda (idempotente)."""
    branch, _ = AnalysisBranch.objects.get_or_create(
        experiment=experiment,
        is_main=True,
        defaults={"name": MAIN_BRANCH_NAME},
    )
    return branch


def resolve_branch(experiment, branch_id) -> AnalysisBranch:
    """Resolve `?branch=` (id) para uma branch ativa do experimento.

    `None` devolve a main — retrocompatível com clientes que não conhecem
    branches.
    """
    if branch_id in (None, ""):
        return ensure_main_branch(experiment)
    return get_object_or_404(
        AnalysisBranch, id=branch_id, experiment=experiment, active=True
    )


@transaction.atomic
def fork_branch(source: AnalysisBranch, name: str, user) -> AnalysisBranch:
    """Materializa uma branch nova copiando as árvores de gates da base.

    Cada gate copiada recebe ``forked_from`` apontando para a origem e
    ``copied_from=None`` — ela não entra na família de cópias da base,
    então propagações em escopo nunca cruzam branches. O snapshot do fork
    (pares + estado da base) é gravado na branch para o merge futuro.
    """
    experiment = source.experiment
    new_branch = AnalysisBranch.objects.create(
        experiment=experiment,
        name=name,
        base_branch=source,
        created_by=user,
    )

    pairs: dict[str, int] = {}
    base: dict[str, dict] = {}
    for fd in FileDataModel.objects.filter(experiment=experiment):
        gate_map: dict[int, GateModel] = {}
        pending = list(
            GateModel.objects.filter(file_data=fd, branch=source).order_by("created_at")
        )
        while pending:
            progressed = False
            for gate in pending[:]:
                if gate.parent_id is not None and gate.parent_id not in gate_map:
                    continue
                new_gate = GateModel.objects.create(
                    file_data=fd,
                    name=gate.name,
                    gate_coordinates=gate.gate_coordinates,
                    plot_config=gate.plot_config,
                    dashboard=gate.dashboard,
                    parent=gate_map.get(gate.parent_id),
                    copied_from=None,
                    forked_from=gate,
                    branch=new_branch,
                    color=gate.color,
                    created_by=user,
                )
                try:
                    result = gate.analysis_result
                except AnalysisResult.DoesNotExist:
                    result = None
                if result is not None:
                    AnalysisResult.objects.create(
                        gate=new_gate, analysis_result=result.analysis_result
                    )
                gate_map[gate.id] = new_gate
                pairs[str(new_gate.id)] = gate.id
                base[str(gate.id)] = {f: getattr(gate, f) for f in MERGE_FIELDS}
                pending.remove(gate)
                progressed = True
            if not progressed:
                break  # ciclo ou pai fora do conjunto — não deveria acontecer

    new_branch.fork_snapshot = {"pairs": pairs, "base": base}
    new_branch.save(update_fields=["fork_snapshot"])

    record_revision(
        experiment=experiment,
        action=AnalysisRevision.ACTION_FORK,
        target_type=AnalysisRevision.TARGET_BRANCH,
        target_id=new_branch.id,
        user=user,
        branch=new_branch,
        payload_after={
            "branch": {"id": new_branch.id, "name": name},
            "base_branch_id": source.id,
            "gates_copied": len(pairs),
        },
        summary=f'criou a branch "{name}" a partir de "{source.name}"',
    )
    return new_branch


# ---------------------------------------------------------------------------
# Diff e merge (source → target)
# ---------------------------------------------------------------------------

FIELD_LABELS = {
    "name": "nome",
    "gate_coordinates": "geometria",
    "plot_config": "visualização",
    "color": "cor",
}


def _changed_fields(current, base_fields: dict) -> dict:
    """Campos de `current` que divergem do estado da base no fork."""
    return {
        f: getattr(current, f)
        for f in MERGE_FIELDS
        if f in base_fields and getattr(current, f) != base_fields[f]
    }


def diff_branches(source: AnalysisBranch, target: AnalysisBranch) -> dict:
    """Diff estrutural `source → target` (preview do merge, sem gravar).

    Devolve ``changes`` (aplicáveis sem decisão) e ``conflicts`` (exigem
    resolução humana por chave: ``f:<a_id>``, ``dt:<b_id>``,
    ``ds:<a_id>``).
    """
    snapshot = source.fork_snapshot or {}
    pairs: dict[str, int] = snapshot.get("pairs", {})
    base: dict[str, dict] = snapshot.get("base", {})
    a_by_b = {int(b): a for b, a in pairs.items()}

    source_gates = {g.id: g for g in GateModel.objects.filter(branch=source)}
    target_gates = {g.id: g for g in GateModel.objects.filter(branch=target)}

    changes: list[dict] = []
    conflicts: list[dict] = []

    for b in source_gates.values():
        a_id = a_by_b.get(b.id)
        if a_id is None:
            changes.append(
                {
                    "type": "create",
                    "source_gate_id": b.id,
                    "file_data_id": b.file_data_id,
                    "file_name": b.file_data.file_name if b.file_data_id else None,
                    "name": b.name,
                    "parent_name": (
                        source_gates[b.parent_id].name
                        if b.parent_id in source_gates
                        else None
                    ),
                }
            )
            continue

        a = target_gates.get(a_id)
        if a is None:
            # A base apagou o gate depois do fork; a filha ainda o tem.
            conflicts.append(
                {
                    "key": f"dt:{b.id}",
                    "type": "deleted_in_target",
                    "source_gate_id": b.id,
                    "target_gate_id": a_id,
                    "file_data_id": b.file_data_id,
                    "name": b.name,
                    "detail": (
                        "O gate foi excluído na branch de destino, mas "
                        "existe nesta branch."
                    ),
                }
            )
            continue

        base_fields = base.get(str(a_id), {})
        b_diff = _changed_fields(b, base_fields)
        a_diff = _changed_fields(a, base_fields)

        field_conflicts = {}
        auto_fields = {}
        for field, b_value in b_diff.items():
            if field in a_diff and a_diff[field] != b_value:
                field_conflicts[field] = {
                    "base": base_fields.get(field),
                    "target": a_diff[field],
                    "source": b_value,
                }
            else:
                auto_fields[field] = b_value

        if field_conflicts:
            conflicts.append(
                {
                    "key": f"f:{a.id}",
                    "type": "modified_both",
                    "source_gate_id": b.id,
                    "target_gate_id": a.id,
                    "file_data_id": a.file_data_id,
                    "name": b.name,
                    "fields": field_conflicts,
                    "auto_fields": auto_fields,
                    "detail": "Editado nas duas branches desde o fork.",
                }
            )
        elif auto_fields:
            changes.append(
                {
                    "type": "update",
                    "source_gate_id": b.id,
                    "target_gate_id": a.id,
                    "file_data_id": a.file_data_id,
                    "name": b.name,
                    "fields": auto_fields,
                }
            )

    # Gates da base cuja cópia sumiu na filha = deletados na source.
    b_ids = set(source_gates)
    for b_id, a_id in a_by_b.items():
        if b_id in b_ids:
            continue
        a = target_gates.get(a_id)
        if a is None:
            continue  # apagado dos dois lados — converge sozinho
        base_fields = base.get(str(a_id), {})
        a_diff = _changed_fields(a, base_fields)
        if a_diff:
            conflicts.append(
                {
                    "key": f"ds:{a.id}",
                    "type": "edited_in_target_deleted_in_source",
                    "target_gate_id": a.id,
                    "file_data_id": a.file_data_id,
                    "name": a.name,
                    "detail": (
                        "O gate foi excluído nesta branch, mas editado "
                        "na de destino."
                    ),
                }
            )
        else:
            changes.append(
                {
                    "type": "delete",
                    "target_gate_id": a.id,
                    "file_data_id": a.file_data_id,
                    "name": a.name,
                }
            )

    return {"changes": changes, "conflicts": conflicts}


def _resolve_target_parent_a(b_gate, source_gates, pairs, created_map):
    """Pai no destino para um gate adicionado: sobe a árvore da source até
    achar um ancestral com par conhecido na base (ou criado neste merge)."""
    parent = source_gates.get(b_gate.parent_id)
    while parent is not None:
        if parent.id in created_map:
            return created_map[parent.id]
        a_id = pairs.get(str(parent.id))
        if a_id is not None and GateModel.objects.filter(pk=a_id).exists():
            return a_id
        parent = source_gates.get(parent.parent_id)
    return None


def _unique_gate_name(file_data_id, branch_id, parent_id, name) -> str:
    candidate = name
    suffix = 2
    while GateModel.objects.filter(
        file_data_id=file_data_id,
        branch_id=branch_id,
        parent_id=parent_id,
        name=candidate,
    ).exists():
        candidate = f"{name} ({suffix})"
        suffix += 1
    return candidate


def _clone_into_target(b_gate, target, parent_id, created_map, *, rename=None):
    name = rename or b_gate.name
    name = _unique_gate_name(b_gate.file_data_id, target.id, parent_id, name)
    new_gate = GateModel.objects.create(
        file_data_id=b_gate.file_data_id,
        name=name,
        gate_coordinates=b_gate.gate_coordinates,
        plot_config=b_gate.plot_config,
        dashboard=b_gate.dashboard,
        parent_id=parent_id,
        copied_from=None,
        forked_from_id=b_gate.forked_from_id or b_gate.id,
        branch=target,
        color=b_gate.color,
        created_by=b_gate.created_by,
    )
    created_map[b_gate.id] = new_gate.id
    return new_gate


@transaction.atomic
def merge_branches(
    source: AnalysisBranch,
    target: AnalysisBranch,
    *,
    resolutions: dict,
    user,
) -> dict:
    """Aplica o diff `source → target` gravando revisões por tipo.

    Cada grupo vira uma revisão padrão (create/delete/update_*) na branch
    alvo — individualmente revertível — e uma revisão `merge` ancora a
    operação citando as constituintes (reverter o merge = reverter a
    cadeia). Conflito sem resolução aborta antes de gravar nada.
    """
    if source.base_branch_id != target.id:
        raise ValueError(
            "Merge só é permitido da branch para a base dela "
            f'("{source.base_branch.name if source.base_branch else "?"}").'
        )

    diff = diff_branches(source, target)
    unresolved = [c for c in diff["conflicts"] if c["key"] not in resolutions]
    if unresolved:
        return {"merged": False, "conflicts": unresolved, "applied": []}

    snapshot = source.fork_snapshot or {}
    pairs: dict[str, int] = snapshot.get("pairs", {})
    source_gates = {g.id: g for g in GateModel.objects.filter(branch=source)}

    revision_ids: list[int] = []
    applied: list[dict] = []
    created_map: dict[int, int] = {}  # b_id → a_id (novos)
    touched_files: set[int] = set()

    # -- Updates (mudança só na source + resoluções "theirs"/auto_fields) --
    updates_by_kind: dict[str, dict] = {
        AnalysisRevision.ACTION_UPDATE_GEOMETRY: {"before": {}, "after": {}, "ids": []},
        AnalysisRevision.ACTION_RENAME: {"before": {}, "after": {}, "ids": []},
        AnalysisRevision.ACTION_RECOLOR: {"before": {}, "after": {}, "ids": []},
    }

    def _queue_update(a_gate, fields: dict):
        before = {f: getattr(a_gate, f) for f in fields}
        for f, v in fields.items():
            setattr(a_gate, f, v)
        a_gate.save(update_fields=list(fields))
        if "gate_coordinates" in fields:
            kind = AnalysisRevision.ACTION_UPDATE_GEOMETRY
        elif "name" in fields:
            kind = AnalysisRevision.ACTION_RENAME
        else:
            kind = AnalysisRevision.ACTION_RECOLOR
        bucket = updates_by_kind[kind]
        bucket["before"][str(a_gate.id)] = before
        bucket["after"][str(a_gate.id)] = fields
        bucket["ids"].append(a_gate.id)
        touched_files.add(a_gate.file_data_id)
        applied.append({"type": "update", "gate_id": a_gate.id, "fields": fields})

    for change in diff["changes"]:
        if change["type"] == "update":
            a = GateModel.objects.get(pk=change["target_gate_id"])
            _queue_update(a, change["fields"])

    for conflict in diff["conflicts"]:
        resolution = resolutions[conflict["key"]]
        if conflict["type"] == "modified_both":
            a = GateModel.objects.get(pk=conflict["target_gate_id"])
            fields = dict(conflict["auto_fields"])
            if resolution == "theirs":
                fields.update({f: v["source"] for f, v in conflict["fields"].items()})
                _queue_update(a, fields)
            elif fields:
                _queue_update(a, fields)
            if resolution == "both":
                b = source_gates[conflict["source_gate_id"]]
                parent_id = _resolve_target_parent_a(
                    b, source_gates, pairs, created_map
                )
                new_gate = _clone_into_target(
                    b,
                    target,
                    parent_id,
                    created_map,
                    rename=b.name,
                )
                applied.append(
                    {
                        "type": "create",
                        "gate_id": new_gate.id,
                        "reason": "conflito resolvido mantendo ambos",
                    }
                )

    for kind, bucket in updates_by_kind.items():
        if not bucket["ids"]:
            continue
        rev = record_revision(
            experiment=target.experiment,
            action=kind,
            target_type=AnalysisRevision.TARGET_GATE,
            target_id=bucket["ids"][0],
            user=user,
            branch=target,
            payload_before={"gates": bucket["before"]},
            payload_after={"gates": bucket["after"]},
            affected_ids=bucket["ids"],
            summary=(
                f'merge de "{source.name}": atualizou ' f'{len(bucket["ids"])} gate(s)'
            ),
        )
        revision_ids.append(rev.id)

    # -- Deletes (deletado na source, intacto na base; ou "theirs" no ds:) --
    delete_ids = [c["target_gate_id"] for c in diff["changes"] if c["type"] == "delete"]
    for conflict in diff["conflicts"]:
        if (
            conflict["type"] == "edited_in_target_deleted_in_source"
            and resolutions[conflict["key"]] == "theirs"
        ):
            delete_ids.append(conflict["target_gate_id"])
    if delete_ids:
        snapshots = {}
        for gid in delete_ids:
            gate = GateModel.objects.filter(pk=gid).first()
            if gate is not None:
                touched_files.add(gate.file_data_id)
                for k, snap in gate_subtree_snapshots(gate).items():
                    snapshots.setdefault(k, snap)
                applied.append({"type": "delete", "gate_id": gid, "name": gate.name})
        GateModel.objects.filter(id__in=delete_ids).delete()
        rev = record_revision(
            experiment=target.experiment,
            action=AnalysisRevision.ACTION_DELETE,
            target_type=AnalysisRevision.TARGET_GATE,
            target_id=delete_ids[0],
            user=user,
            branch=target,
            payload_before={"gates": snapshots},
            affected_ids=[int(g) for g in snapshots],
            summary=(f'merge de "{source.name}": excluiu ' f"{len(snapshots)} gate(s)"),
        )
        revision_ids.append(rev.id)

    # -- Creates (adicionados na source; "theirs" recriando deletados na
    #    base) --
    created_snapshots: dict[str, dict] = {}
    pending_creates = [
        c["source_gate_id"] for c in diff["changes"] if c["type"] == "create"
    ]
    for conflict in diff["conflicts"]:
        if (
            conflict["type"] == "deleted_in_target"
            and resolutions[conflict["key"]] == "theirs"
        ):
            pending_creates.append(conflict["source_gate_id"])

    # Pais antes dos filhos, iterando até estabilizar.
    while pending_creates:
        progressed = False
        for b_id in pending_creates[:]:
            b = source_gates.get(b_id)
            if b is None:
                pending_creates.remove(b_id)
                continue
            if (
                b.parent_id in source_gates
                and source_gates[b.parent_id].id in pending_creates
            ):
                continue  # pai ainda não criado
            parent_id = _resolve_target_parent_a(b, source_gates, pairs, created_map)
            new_gate = _clone_into_target(b, target, parent_id, created_map)
            created_snapshots[str(new_gate.id)] = gate_snapshot(new_gate)
            touched_files.add(new_gate.file_data_id)
            applied.append(
                {"type": "create", "gate_id": new_gate.id, "name": new_gate.name}
            )
            pending_creates.remove(b_id)
            progressed = True
        if not progressed:
            # Pai apagado na base — pendura na raiz e segue.
            b_id = pending_creates[0]
            b = source_gates[b_id]
            new_gate = _clone_into_target(b, target, None, created_map)
            created_snapshots[str(new_gate.id)] = gate_snapshot(new_gate)
            touched_files.add(new_gate.file_data_id)
            applied.append(
                {"type": "create", "gate_id": new_gate.id, "name": new_gate.name}
            )
            pending_creates.remove(b_id)

    # "both" já criou gates via _clone_into_target — registrar snapshots.
    for b_id, a_id in created_map.items():
        if str(a_id) not in created_snapshots:
            gate = GateModel.objects.filter(pk=a_id).first()
            if gate is not None:
                created_snapshots[str(a_id)] = gate_snapshot(gate)

    if created_snapshots:
        first_id = int(next(iter(created_snapshots)))
        rev = record_revision(
            experiment=target.experiment,
            action=AnalysisRevision.ACTION_CREATE,
            target_type=AnalysisRevision.TARGET_GATE,
            target_id=first_id,
            user=user,
            branch=target,
            payload_after={"gates": created_snapshots},
            affected_ids=[int(g) for g in created_snapshots],
            summary=(
                f'merge de "{source.name}": criou ' f"{len(created_snapshots)} gate(s)"
            ),
        )
        revision_ids.append(rev.id)

    # -- Marco do merge --
    merge_rev = record_revision(
        experiment=target.experiment,
        action=AnalysisRevision.ACTION_MERGE,
        target_type=AnalysisRevision.TARGET_BRANCH,
        target_id=target.id,
        user=user,
        branch=target,
        payload_after={
            "source_branch_id": source.id,
            "source_branch_name": source.name,
            "revision_ids": revision_ids,
            "resolutions": resolutions,
        },
        affected_ids=revision_ids,
        summary=(
            f'mergeou "{source.name}" em "{target.name}" — '
            f"{len(applied)} mudança(s)"
        ),
    )

    return {
        "merged": True,
        "conflicts": [],
        "applied": applied,
        "touched_files": touched_files,
        "merge_revision_id": merge_rev.id,
    }


def refresh_after_merge(touched_files: set[int]) -> None:
    """Invalida densidade e recalcula raízes das amostras tocadas pelo merge."""
    from analytics.tasks import recalculate_gate_analysis
    from utils.density import invalidate_density

    for fd_id in touched_files:
        invalidate_density(fd_id)
        for gate in GateModel.objects.filter(file_data_id=fd_id, parent__isnull=True):
            recalculate_gate_analysis(gate.id)
