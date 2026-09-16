"""Registro e reversão do histórico de análise (BE-08, ADR-0008).

`record_revision` é o único ponto de escrita do log: toda mutação de
análise chama aqui com o alvo, a ação, os payloads antes/depois e uma
`summary` pronta para a UI. O log é append-only — reverter uma revisão
aplica o inverso e grava uma revisão nova (`action="revert"`), nunca
reescreve a original.

`plan_revert` calcula o que a reversão mudaria (e os conflitos que a
bloqueiam) sem gravar nada — é o que alimenta o `dry_run` do endpoint de
reversão e o preview do detalhe.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction

from analytics.gate_author import author_display_name
from analytics.models import (
    AnalysisRevision,
    DashboardModel,
    GateModel,
)

REVERSIBLE_ACTIONS = {
    AnalysisRevision.ACTION_CREATE,
    AnalysisRevision.ACTION_UPDATE_GEOMETRY,
    AnalysisRevision.ACTION_RENAME,
    AnalysisRevision.ACTION_RECOLOR,
    AnalysisRevision.ACTION_APPLY,
    AnalysisRevision.ACTION_DELETE,
    AnalysisRevision.ACTION_DISABLE,
    AnalysisRevision.ACTION_ENABLE,
    AnalysisRevision.ACTION_MOVE_SUBSAMPLE,
    AnalysisRevision.ACTION_COMPENSATION_APPLY,
    AnalysisRevision.ACTION_COMPENSATION_REMOVE,
    AnalysisRevision.ACTION_REVERT,
    AnalysisRevision.ACTION_RESTORE,
}

# Inatividade que separa sessões na timeline (auto-checkpoints derivados,
# ADR-0017). Configurável via env ANALYSIS_SESSION_GAP_MINUTES (default 15).
SESSION_GAP_MINUTES = settings.ANALYSIS_SESSION_GAP_MINUTES

# Campos de gate que uma edição pode tocar — usados nos snapshots e na
# verificação "o alvo mudou depois" da reversão.
GATE_FIELDS = ("name", "color", "gate_coordinates", "plot_config", "copied_from_id")


def gate_snapshot(gate: GateModel) -> dict:
    """Campos necessários para comparar ou recriar o gate depois."""
    return {
        "name": gate.name,
        "color": gate.color,
        "gate_coordinates": gate.gate_coordinates,
        "plot_config": gate.plot_config,
        "copied_from_id": gate.copied_from_id,
        "parent_id": gate.parent_id,
        "file_data_id": gate.file_data_id,
        "dashboard_id": gate.dashboard_id,
    }


def gate_subtree_snapshots(gate: GateModel) -> dict:
    """Snapshot do gate e de toda a subárvore, pais antes dos filhos.

    As chaves são os ids antigos — na recriação (revert de delete) o mapa
    antigo→novo resolve os `parent_id` internos.
    """
    snapshots = {}
    queue = [gate]
    while queue:
        current = queue.pop(0)
        snapshots[str(current.id)] = gate_snapshot(current)
        queue.extend(current.children.all())
    return snapshots


def _infer_file_data_id(target_type, target_id, payload_before, payload_after):
    """A amostra que a revisão toca, ou None quando é experiment-wide.

    Gates vêm dos snapshots do payload (`file_data_id` gravado no
    snapshot) — funciona mesmo quando o gate já foi deletado. Fallback:
    o gate ainda vivo no banco. Revisões de subsample/compensação/
    experimento ficam NULL e aparecem em todo recorte por amostra.
    """
    if target_type == AnalysisRevision.TARGET_FILE:
        return target_id
    if target_type == AnalysisRevision.TARGET_GATE:
        for payload in (payload_after, payload_before):
            for snap in ((payload or {}).get("gates") or {}).values():
                if isinstance(snap, dict) and snap.get("file_data_id"):
                    return snap["file_data_id"]
        gate = GateModel.objects.filter(pk=target_id).only("file_data_id").first()
        return gate.file_data_id if gate else None
    return None


def record_revision(
    *,
    experiment,
    action,
    target_type,
    target_id,
    user,
    scope=None,
    payload_before=None,
    payload_after=None,
    affected_ids=None,
    summary,
    reverts=None,
    file_data=None,
) -> AnalysisRevision:
    file_data_id = (
        getattr(file_data, "id", file_data)
        if file_data is not None
        else _infer_file_data_id(target_type, target_id, payload_before, payload_after)
    )
    return AnalysisRevision.objects.create(
        experiment=experiment,
        action=action,
        target_type=target_type,
        target_id=target_id,
        user=user if getattr(user, "is_authenticated", False) else None,
        scope=scope,
        payload_before=payload_before or {},
        payload_after=payload_after or {},
        affected_ids=affected_ids or [],
        summary=summary,
        reverts=reverts,
        file_data_id=file_data_id,
    )


def author_name(user) -> str:
    if not user:
        return "—"
    return author_display_name(user.first_name, user.last_name, user.username)


def revision_dict(revision: AnalysisRevision) -> dict:
    return {
        "id": revision.id,
        "action": revision.action,
        "scope": revision.scope,
        "target": {"type": revision.target_type, "id": revision.target_id},
        "summary": revision.summary,
        "author": author_name(revision.user),
        "affected_ids": revision.affected_ids,
        "created_at": revision.created_at,
        "reverts": revision.reverts_id,
        "revertible": revision.action in REVERSIBLE_ACTIONS,
    }


# ---------------------------------------------------------------------------
# Reversão
# ---------------------------------------------------------------------------


def _gate_field_conflicts(
    revision: AnalysisRevision,
    force: bool = False,
    pending_recreate: set = frozenset(),
) -> tuple[list, list]:
    """Confere cada alvo de uma edição de campos (rename/recolor/geometry).

    Retorna (mudanças, conflitos): para cada gate registrado, exige que ele
    exista e que os campos tocados continuem com o valor `after` — se o
    gate foi alterado depois, a reversão é bloqueada em vez de sobrescrever
    às cegas. Com `force=True` o drift vira sobrescrita (o `before` é
    aplicado mesmo assim); conflitos estruturais (gate inexistente, nome
    em uso) continuam bloqueando. `pending_recreate` cobre a cadeia de
    restore: um gate que será recriado por um revert de delete anterior na
    cadeia recebe `old_gate_id` e é resolvido no apply via `id_map`.
    """
    changes = []
    conflicts = []
    before = revision.payload_before.get("gates", {})
    after = revision.payload_after.get("gates", {})
    for gid, before_fields in before.items():
        gate = GateModel.objects.filter(pk=int(gid)).first()
        if gate is None:
            if int(gid) in pending_recreate:
                changes.append({"old_gate_id": int(gid), "fields": before_fields})
            else:
                conflicts.append(
                    {"gate_id": int(gid), "detail": "Gate não existe mais."}
                )
            continue
        after_fields = after.get(gid, {})
        drifted = [
            field
            for field, value in after_fields.items()
            if getattr(gate, field) != value
        ]
        if drifted and not force:
            conflicts.append(
                {
                    "gate_id": gate.id,
                    "detail": "O gate foi alterado depois desta revisão.",
                    "fields": drifted,
                }
            )
            continue
        new_name = before_fields.get("name")
        if new_name is not None and new_name != gate.name:
            clash = (
                GateModel.objects.filter(
                    file_data_id=gate.file_data_id,
                    parent_id=gate.parent_id,
                    name=new_name,
                )
                .exclude(id=gate.id)
                .exists()
            )
            if clash:
                conflicts.append(
                    {
                        "gate_id": gate.id,
                        "detail": f'O nome "{new_name}" já está em uso neste nível.',
                    }
                )
                continue
        changes.append({"gate_id": gate.id, "fields": before_fields})
    return changes, conflicts


def _plan_gate_update_revert(
    revision: AnalysisRevision,
    force: bool = False,
    pending_recreate: set = frozenset(),
):
    changes, conflicts = _gate_field_conflicts(
        revision, force=force, pending_recreate=pending_recreate
    )
    return {
        "kind": "gate_fields",
        "changes": changes,
        "conflicts": conflicts,
    }


def _plan_gate_create_revert(
    revision: AnalysisRevision, pending_recreate: set = frozenset()
):
    """Reverter uma criação = excluir o gate criado."""
    created = revision.payload_after.get("gates", {})
    changes = []
    conflicts = []
    for gid in created:
        gate = GateModel.objects.filter(pk=int(gid)).first()
        if gate is None:
            if int(gid) in pending_recreate:
                # O gate foi deletado e um revert anterior na cadeia vai
                # recriá-lo — o delete resolve via `id_map` no apply.
                changes.append(
                    {"old_gate_id": int(gid), "name": created[gid].get("name")}
                )
            else:
                conflicts.append({"gate_id": int(gid), "detail": "Gate já não existe."})
            continue
        children = gate.children.count()
        changes.append(
            {
                "gate_id": gate.id,
                "name": gate.name,
                "children_deleted": children,
            }
        )
    return {"kind": "gate_delete", "changes": changes, "conflicts": conflicts}


def _plan_gate_delete_revert(
    revision: AnalysisRevision, pending_recreate: set = frozenset()
):
    """Reverter uma exclusão = recriar as linhas do snapshot (pais primeiro)."""
    snapshots = revision.payload_before.get("gates", {})
    changes = []
    conflicts = []
    for gid, snap in snapshots.items():
        parent_id = snap.get("parent_id")
        parent_known = parent_id is None or str(parent_id) in snapshots
        parent_alive = (
            parent_id is None
            or int(parent_id) in pending_recreate
            or GateModel.objects.filter(pk=parent_id).exists()
        )
        if not parent_known and not parent_alive:
            conflicts.append(
                {
                    "gate_id": int(gid),
                    "name": snap.get("name"),
                    "detail": "O gate pai não existe mais.",
                }
            )
            continue
        changes.append({"old_gate_id": int(gid), "snapshot": snap})
    return {"kind": "gate_recreate", "changes": changes, "conflicts": conflicts}


def _plan_apply_revert(revision: AnalysisRevision, pending_recreate: set = frozenset()):
    """Reverter um apply: apaga os gates criados e restaura os substituídos."""
    created = revision.payload_after.get("created_gate_ids", [])
    replaced = revision.payload_before.get("replaced", {})
    changes = []
    conflicts = []
    for gid in created:
        gate = GateModel.objects.filter(pk=gid).first()
        if gate is None:
            continue  # já foi removido; nada a fazer
        changes.append({"gate_id": gate.id, "name": gate.name, "op": "delete"})
    for gid, snap in replaced.items():
        gate = GateModel.objects.filter(pk=int(gid)).first()
        if gate is None:
            if int(gid) in pending_recreate:
                changes.append(
                    {"old_gate_id": int(gid), "fields": snap, "op": "restore"}
                )
            else:
                conflicts.append(
                    {
                        "gate_id": int(gid),
                        "detail": "Gate substituído não existe mais.",
                    }
                )
            continue
        changes.append({"gate_id": gate.id, "fields": snap, "op": "restore"})
    return {"kind": "apply", "changes": changes, "conflicts": conflicts}


_TARGET_MODELS = {}


def _target_model(target_type):
    from fcs_parser.models import (
        ExperimentModel,
        FileDataModel,
        SubsampleModel,
    )

    if not _TARGET_MODELS:
        _TARGET_MODELS.update(
            {
                AnalysisRevision.TARGET_FILE: FileDataModel,
                AnalysisRevision.TARGET_SUBSAMPLE: SubsampleModel,
                AnalysisRevision.TARGET_EXPERIMENT: ExperimentModel,
            }
        )
    return _TARGET_MODELS[target_type]


def _plan_target_fields_revert(revision: AnalysisRevision, force: bool = False):
    """Reversão genérica de campos em amostra/subsample/experimento.

    Cobre disable/enable (campo `active`), move_subsample (`subsample_id`)
    e rename de subsample (`name`): exige que os campos tocados continuem
    com o valor `after` — drift bloqueia a reversão (com `force=True` o
    `before` sobrescreve mesmo com drift).
    """
    from fcs_parser.models import SubsampleModel

    model = _target_model(revision.target_type)
    before = revision.payload_before.get("targets", {})
    after = revision.payload_after.get("targets", {})
    changes = []
    conflicts = []
    for tid, before_fields in before.items():
        obj = model.objects.filter(pk=int(tid)).first()
        if obj is None:
            conflicts.append(
                {"target_id": int(tid), "detail": "Registro não existe mais."}
            )
            continue
        after_fields = after.get(tid, {})
        drifted = [
            field
            for field, value in after_fields.items()
            if getattr(obj, field) != value
        ]
        if drifted and not force:
            conflicts.append(
                {
                    "target_id": obj.id,
                    "detail": "O registro foi alterado depois desta revisão.",
                    "fields": drifted,
                }
            )
            continue
        new_name = before_fields.get("name")
        if (
            new_name is not None
            and isinstance(obj, SubsampleModel)
            and SubsampleModel.objects.filter(
                experiment_id=obj.experiment_id, name=new_name
            )
            .exclude(id=obj.id)
            .exists()
        ):
            conflicts.append(
                {
                    "target_id": obj.id,
                    "detail": f'O nome "{new_name}" já está em uso.',
                }
            )
            continue
        new_sub = before_fields.get("subsample_id")
        if (
            new_sub is not None
            and not SubsampleModel.objects.filter(pk=new_sub, active=True).exists()
        ):
            conflicts.append(
                {
                    "target_id": obj.id,
                    "detail": "O subsample de destino não existe mais.",
                }
            )
            continue
        changes.append({"obj": obj, "fields": before_fields})

    # Subsample reativado precisa religar as amostras que saíram dele.
    if revision.target_type == AnalysisRevision.TARGET_SUBSAMPLE:
        from fcs_parser.models import FileDataModel

        for fid in revision.payload_before.get("unlinked_file_ids", []):
            fd = FileDataModel.objects.filter(pk=fid).first()
            if fd is None:
                continue
            if fd.subsample_id is not None:
                conflicts.append(
                    {
                        "target_id": fid,
                        "detail": "A amostra foi movida para outro subsample.",
                    }
                )
                continue
            changes.append({"obj": fd, "fields": {"subsample_id": revision.target_id}})
    return {"kind": "target_fields", "changes": changes, "conflicts": conflicts}


def plan_revert(
    revision: AnalysisRevision,
    force: bool = False,
    pending_recreate: set = frozenset(),
) -> dict:
    """O que a reversão faria — e o que a bloqueia — sem gravar nada.

    `force=True` relaxa a checagem de drift (o `before` sobrescreve edições
    posteriores); conflitos estruturais (alvo inexistente, nome em uso)
    continuam bloqueando mesmo sob force. `pending_recreate` = ids de gates
    que um revert de delete anterior na cadeia de restore vai recriar —
    mudanças sobre eles saem com `old_gate_id` e resolvem no apply.
    """
    if revision.action not in REVERSIBLE_ACTIONS:
        return {"kind": "none", "changes": [], "conflicts": []}
    if revision.action == AnalysisRevision.ACTION_RESTORE:
        return _plan_restore_revert(revision, force=force)
    if revision.action in (
        AnalysisRevision.ACTION_RENAME,
        AnalysisRevision.ACTION_RECOLOR,
        AnalysisRevision.ACTION_UPDATE_GEOMETRY,
        AnalysisRevision.ACTION_REVERT,
    ):
        if revision.target_type == AnalysisRevision.TARGET_GATE:
            return _plan_gate_update_revert(
                revision, force=force, pending_recreate=pending_recreate
            )
        return _plan_target_fields_revert(revision, force=force)
    if revision.action == AnalysisRevision.ACTION_DELETE:
        if revision.target_type == AnalysisRevision.TARGET_GATE:
            return _plan_gate_delete_revert(revision, pending_recreate=pending_recreate)
        return _plan_target_fields_revert(revision, force=force)
    if revision.action == AnalysisRevision.ACTION_CREATE:
        if revision.target_type == AnalysisRevision.TARGET_GATE:
            return _plan_gate_create_revert(revision, pending_recreate=pending_recreate)
        # Subsample criado pela UI: reverter é inativá-lo de novo.
        return _plan_create_inactivate_revert(revision)
    if revision.action == AnalysisRevision.ACTION_APPLY:
        return _plan_apply_revert(revision, pending_recreate=pending_recreate)
    if revision.action in (
        AnalysisRevision.ACTION_COMPENSATION_APPLY,
        AnalysisRevision.ACTION_COMPENSATION_REMOVE,
    ):
        return _plan_compensation_revert(revision, force=force)
    if revision.action in (
        AnalysisRevision.ACTION_DISABLE,
        AnalysisRevision.ACTION_ENABLE,
        AnalysisRevision.ACTION_MOVE_SUBSAMPLE,
    ):
        return _plan_target_fields_revert(revision, force=force)
    return {"kind": "none", "changes": [], "conflicts": []}


def _plan_compensation_revert(revision: AnalysisRevision, force: bool = False) -> dict:
    """Reverter um apply/remove = devolver a matriz aplicada ao `before`.

    Drift: a matriz aplicada hoje difere do `after` da revisão → conflito,
    a menos que `force`. Estrutural: a matriz do `before` descartada
    bloqueia mesmo sob force (não há o que religar).
    """
    from analytics.models import CompensationMatrix

    def _comp(payload):
        return (
            (payload.get("targets") or {})
            .get(str(revision.experiment_id), {})
            .get("compensation")
        )

    set_to = _comp(revision.payload_before)
    after = _comp(revision.payload_after)
    conflicts = []
    if set_to is not None:
        matrix = CompensationMatrix.objects.filter(pk=set_to).first()
        if matrix is None or not matrix.active:
            conflicts.append(
                {
                    "target_id": set_to,
                    "detail": "A matriz de compensação foi descartada.",
                }
            )
    current = CompensationMatrix.objects.filter(
        experiment=revision.experiment, is_applied=True
    ).first()
    current_id = current.id if current else None
    if not force and current_id != after:
        conflicts.append(
            {
                "target_id": revision.target_id,
                "detail": "A compensação aplicada mudou depois desta revisão.",
            }
        )
    return {
        "kind": "compensation",
        "set_to": set_to,
        "changes": [{"compensation": set_to, "target_id": revision.target_id}],
        "conflicts": conflicts,
    }


def _plan_create_inactivate_revert(revision: AnalysisRevision):
    """Reverter a criação de um subsample/arquivo = inativá-lo."""
    model = _target_model(revision.target_type)
    changes = []
    conflicts = []
    obj = model.objects.filter(pk=revision.target_id).first()
    if obj is None:
        conflicts.append(
            {"target_id": revision.target_id, "detail": "Registro não existe mais."}
        )
    elif not obj.active:
        conflicts.append({"target_id": obj.id, "detail": "O registro já está inativo."})
    else:
        changes.append({"obj": obj, "fields": {"active": False}})
    return {"kind": "target_fields", "changes": changes, "conflicts": conflicts}


def _apply_plan(
    revision: AnalysisRevision, plan: dict, shared_id_map: dict | None = None
) -> dict:
    """Executa o plano calculado por `plan_revert`. Retorna o que mudou."""
    kind = plan["kind"]
    applied = []
    skipped = []
    recreated_gate_ids = []
    id_map = shared_id_map if shared_id_map is not None else {}

    def _resolve_gid(change):
        raw = change.get("gate_id", change.get("old_gate_id"))
        return id_map.get(raw, raw)

    if kind == "chain":
        # Reversão de um restore: reaplica os efeitos desfeitos em ordem.
        for sub in plan["plans"]:
            result = _apply_plan(revision, sub, shared_id_map=id_map)
            applied.extend(result["applied"])
            skipped.extend(result["skipped"])
            recreated_gate_ids.extend(result["recreated_gate_ids"])
        return {
            "applied": applied,
            "skipped": skipped,
            "recreated_gate_ids": recreated_gate_ids,
        }

    if kind == "gate_fields":
        for change in plan["changes"]:
            gate = GateModel.objects.filter(pk=_resolve_gid(change)).first()
            if gate is None:
                skipped.append(
                    {"gate_id": change.get("gate_id") or change.get("old_gate_id")}
                )
                continue
            fields = change["fields"]
            update_fields = []
            for field, value in fields.items():
                if field == "copied_from_id":
                    gate.copied_from_id = value
                    update_fields.append("copied_from")
                else:
                    setattr(gate, field, value)
                    update_fields.append(field)
            gate.save(update_fields=update_fields)
            applied.append({"gate_id": gate.id, "fields": update_fields})

    elif kind == "gate_delete":
        for change in plan["changes"]:
            gid = _resolve_gid(change)
            if gid is None or not GateModel.objects.filter(pk=gid).exists():
                skipped.append(
                    {"gate_id": change.get("gate_id") or change.get("old_gate_id")}
                )
                continue
            GateModel.objects.filter(pk=gid).delete()
            applied.append({"gate_id": gid, "deleted": True})

    elif kind == "gate_recreate":
        for change in plan["changes"]:
            snap = change["snapshot"]
            old_id = change["old_gate_id"]
            parent_id = snap.get("parent_id")
            if parent_id is not None:
                parent_id = id_map.get(parent_id, parent_id)
            dashboard = DashboardModel.objects.filter(
                pk=snap.get("dashboard_id")
            ).first()
            if dashboard is None:
                dashboard = DashboardModel.objects.create(
                    name=f"reverted_{old_id}"[:50],
                    file_data_id=snap.get("file_data_id"),
                    dashboard_config={},
                )
            copied_from_id = snap.get("copied_from_id")
            if copied_from_id is not None:
                copied_from_id = id_map.get(copied_from_id, copied_from_id)
                if not GateModel.objects.filter(pk=copied_from_id).exists():
                    copied_from_id = None
            new_gate = GateModel.objects.create(
                file_data_id=snap.get("file_data_id"),
                name=snap.get("name"),
                color=snap.get("color"),
                gate_coordinates=snap.get("gate_coordinates") or {},
                plot_config=snap.get("plot_config") or {},
                dashboard=dashboard,
                parent_id=parent_id,
                copied_from_id=copied_from_id,
            )
            id_map[old_id] = new_gate.id
            recreated_gate_ids.append(new_gate.id)
            applied.append({"old_gate_id": old_id, "gate_id": new_gate.id})

    elif kind == "apply":
        for change in plan["changes"]:
            gid = _resolve_gid(change)
            if change["op"] == "delete":
                GateModel.objects.filter(pk=gid).delete()
            else:
                gate = GateModel.objects.filter(pk=gid).first()
                if gate is None:
                    skipped.append({"gate_id": gid})
                    continue
                update_fields = []
                for field, value in change["fields"].items():
                    if field == "copied_from_id":
                        gate.copied_from_id = value
                        update_fields.append("copied_from")
                    elif field in ("parent_id", "file_data_id", "dashboard_id"):
                        continue  # posição na árvore não é restaurada pelo apply
                    else:
                        setattr(gate, field, value)
                        update_fields.append(field)
                if update_fields:
                    gate.save(update_fields=update_fields)
            applied.append(change)

    elif kind == "compensation":
        from analytics.models import CompensationMatrix
        from fcs_parser.services.compensation import (
            _switch_applied,
            refresh_analysis_views,
        )

        set_to = plan.get("set_to")
        matrix = (
            CompensationMatrix.objects.filter(pk=set_to, active=True).first()
            if set_to is not None
            else None
        )
        if set_to is not None and matrix is None:
            skipped.append({"compensation": set_to})
        else:
            _switch_applied(revision.experiment, matrix)
            refresh_analysis_views(revision.experiment)
            applied.append({"compensation": set_to})

    elif kind == "target_fields":
        for change in plan["changes"]:
            obj = change["obj"]
            update_fields = []
            for field, value in change["fields"].items():
                setattr(obj, field, value)
                update_fields.append(field)
            if "active" in change["fields"] and hasattr(obj, "deactivated_at"):
                obj.deactivated_at = (
                    None if change["fields"]["active"] else obj.deactivated_at
                )
                obj.deactivated_by = None
                update_fields += ["deactivated_at", "deactivated_by"]
            obj.save(update_fields=update_fields)
            applied.append({"target_id": obj.id, "fields": update_fields})

    return {
        "applied": applied,
        "skipped": skipped,
        "recreated_gate_ids": recreated_gate_ids,
    }


def public_changes(plan: dict) -> list:
    """`plan["changes"]` sem objetos de modelo — versão segura p/ resposta."""
    if plan.get("kind") == "chain":
        out = []
        for sub in plan["plans"]:
            out.extend(public_changes(sub))
        return out
    out = []
    for change in plan["changes"]:
        entry = {k: v for k, v in change.items() if k != "obj"}
        if "obj" in change:
            entry["target_id"] = change["obj"].id
        out.append(entry)
    return out


@transaction.atomic
def apply_revert(revision: AnalysisRevision, user) -> dict:
    """Aplica o inverso da revisão e registra a reversão como evento novo."""
    plan = plan_revert(revision)
    if plan["conflicts"]:
        return {"conflicts": plan["conflicts"], "would_change": []}
    result = _apply_plan(revision, plan)
    record_revision(
        experiment=revision.experiment,
        action=AnalysisRevision.ACTION_REVERT,
        target_type=revision.target_type,
        target_id=revision.target_id,
        user=user,
        payload_before=revision.payload_after,
        payload_after=revision.payload_before,
        affected_ids=revision.affected_ids,
        summary=f"reverteu: {revision.summary}",
        reverts=revision,
    )
    return {
        "conflicts": [],
        "would_change": public_changes(plan),
        "applied": result["applied"],
    }


# ---------------------------------------------------------------------------
# Sessões, checkpoints e restauração por ponto (BE-20, ADR-0017)
# ---------------------------------------------------------------------------


def _revisions_after(experiment, target_revision) -> list:
    """Revisões posteriores ao alvo, mais novas primeiro.

    `target_revision=None` = estado inicial do experimento (todas as
    revisões entram na cadeia).
    """
    qs = AnalysisRevision.objects.filter(experiment=experiment).order_by("-id")
    if target_revision is not None:
        qs = qs.filter(id__gt=target_revision.id)
    return list(qs)


def group_into_sessions(revisions) -> list[dict]:
    """Agrupa revisões (mais novas primeiro) em sessões de atividade.

    Auto-checkpoint derivado na leitura (ADR-0017): um gap maior que
    SESSION_GAP_MINUTES entre revisões consecutivas fecha a sessão. Nada é
    gravado — `end_revision_id` é a borda restaurável da sessão.
    """
    gap = timedelta(minutes=SESSION_GAP_MINUTES)
    sessions = []
    for rev in revisions:
        if sessions and (sessions[-1]["_prev_at"] - rev.created_at) <= gap:
            sessions[-1]["revisions"].append(rev)
        else:
            sessions.append({"revisions": [rev], "_prev_at": rev.created_at})
        sessions[-1]["_prev_at"] = rev.created_at
    out = []
    for s in sessions:
        revs = s["revisions"]
        out.append(
            {
                "start_revision_id": revs[-1].id,
                "end_revision_id": revs[0].id,
                "started_at": revs[-1].created_at,
                "ended_at": revs[0].created_at,
                "count": len(revs),
                "revisions": revs,
            }
        )
    return out


def plan_restore(experiment, target_revision, force: bool = False) -> dict:
    """Plano composto do restore: reverter em cadeia (mais nova primeiro)
    tudo que veio depois da revisão-alvo.

    Cada conflito é anotado com o `revision_id` que o gerou. Sem `force`,
    qualquer conflito bloqueia o restore inteiro (atômico); com `force`, o
    drift é sobrescrito e só conflitos estruturais restam.
    """
    plans = []
    conflicts = []
    pending_recreate = set()
    for rev in _revisions_after(experiment, target_revision):
        plan = plan_revert(rev, force=force, pending_recreate=pending_recreate)
        for c in plan["conflicts"]:
            conflicts.append({**c, "revision_id": rev.id})
        if plan["kind"] != "none" or plan["conflicts"]:
            plans.append({"revision": rev, "plan": plan})
        if plan["kind"] == "gate_recreate":
            pending_recreate.update(c["old_gate_id"] for c in plan["changes"])
    return {"plans": plans, "conflicts": conflicts}


def _public_plan(plan: dict) -> dict:
    return {
        "kind": plan["kind"],
        "changes": public_changes(plan),
        "conflicts": plan["conflicts"],
    }


def apply_restore(experiment, target_revision, user, force: bool = False) -> dict:
    """Restaura o experimento ao estado da revisão-alvo.

    Atômico por padrão (conflito → `blocked`); `force=True` sobrescreve o
    drift. Grava UMA revisão `action="restore"` com o antes de cada
    operação e o que foi desfeito — suficiente para reverter o próprio
    restore (replay forward) e auditar depois. Gates recriados são
    recalculados após o commit (stats voltam, incluindo o marcador
    `applicable:false` do ADR-0016 quando couber).
    """
    outcome = plan_restore(experiment, target_revision, force=force)
    if outcome["conflicts"] and not force:
        return {
            "blocked": True,
            "conflicts": outcome["conflicts"],
            "would_change": [
                {"revision_id": item["revision"].id, **_public_plan(item["plan"])}
                for item in outcome["plans"]
            ],
        }

    applied = []
    recreated_ids = []
    id_map = {}
    with transaction.atomic():
        for item in outcome["plans"]:
            rev, plan = item["revision"], item["plan"]
            result = _apply_plan(rev, plan, shared_id_map=id_map)
            applied.append({"revision_id": rev.id, "applied": result["applied"]})
            recreated_ids.extend(result["recreated_gate_ids"])
        record_revision(
            experiment=experiment,
            action=AnalysisRevision.ACTION_RESTORE,
            target_type=AnalysisRevision.TARGET_EXPERIMENT,
            target_id=experiment.id,
            user=user,
            payload_before={
                "plans": [
                    {"revision_id": item["revision"].id, **_public_plan(item["plan"])}
                    for item in outcome["plans"]
                ]
            },
            payload_after={
                "restored_to": target_revision.id if target_revision else None,
                "undone": [
                    {
                        "revision_id": item["revision"].id,
                        "action": item["revision"].action,
                        "target_type": item["revision"].target_type,
                        "payload_before": item["revision"].payload_before,
                        "payload_after": item["revision"].payload_after,
                    }
                    for item in reversed(outcome["plans"])
                ],
                "recreated_id_map": id_map,
            },
            affected_ids=[item["revision"].id for item in outcome["plans"]],
            summary=(
                f"restaurou o estado de {target_revision.summary!r}"
                if target_revision
                else "restaurou o estado inicial do experimento"
            )[:512],
            reverts=target_revision,
        )

    from analytics.tasks import recalculate_gate_analysis

    for gid in recreated_ids:
        recalculate_gate_analysis(gid)

    return {
        "blocked": False,
        "applied": applied,
        "skipped": outcome["conflicts"],
    }


def _plan_restore_revert(revision: AnalysisRevision, force: bool = False) -> dict:
    """Reverter um restore = refazer, em ordem cronológica, o que ele desfez.

    O payload_after do restore carrega os `payload_*` de cada revisão
    desfeita e o mapa old→new dos gates recriados — replay auto-contido.
    """
    undone = revision.payload_after.get("undone", [])
    id_map = {
        int(old): new
        for old, new in revision.payload_after.get("recreated_id_map", {}).items()
    }
    subplans = []
    conflicts = []
    for entry in sorted(undone, key=lambda e: e["revision_id"]):
        plan = _forward_plan(entry, id_map)
        subplans.append(plan)
        for c in plan["conflicts"]:
            conflicts.append({**c, "revision_id": entry["revision_id"]})
    return {"kind": "chain", "plans": subplans, "conflicts": conflicts}


def _forward_plan(entry: dict, id_map: dict) -> dict:
    """Plano que REFAZ o efeito de uma revisão desfeita por um restore."""
    action = entry["action"]
    after = entry.get("payload_after") or {}
    before = entry.get("payload_before") or {}
    target_type = entry["target_type"]

    def mid(gid):
        return id_map.get(int(gid), int(gid))

    gate_field_actions = {
        AnalysisRevision.ACTION_RENAME,
        AnalysisRevision.ACTION_RECOLOR,
        AnalysisRevision.ACTION_UPDATE_GEOMETRY,
        AnalysisRevision.ACTION_REVERT,
    }
    if action in gate_field_actions and target_type == AnalysisRevision.TARGET_GATE:
        changes, conflicts = [], []
        for gid, fields in after.get("gates", {}).items():
            gate = GateModel.objects.filter(pk=mid(gid)).first()
            if gate is None:
                conflicts.append(
                    {"gate_id": mid(gid), "detail": "Gate não existe mais."}
                )
            else:
                changes.append({"gate_id": gate.id, "fields": fields})
        return {"kind": "gate_fields", "changes": changes, "conflicts": conflicts}
    if action == AnalysisRevision.ACTION_CREATE and target_type == "gate":
        changes = [
            {"old_gate_id": int(gid), "snapshot": snap}
            for gid, snap in after.get("gates", {}).items()
        ]
        return {"kind": "gate_recreate", "changes": changes, "conflicts": []}
    if action == AnalysisRevision.ACTION_DELETE and target_type == "gate":
        changes, conflicts = [], []
        for gid in before.get("gates", {}):
            gate = GateModel.objects.filter(pk=mid(gid)).first()
            if gate is None:
                conflicts.append(
                    {"gate_id": mid(gid), "detail": "Gate recriado não existe mais."}
                )
            else:
                changes.append({"gate_id": gate.id, "name": gate.name})
        return {"kind": "gate_delete", "changes": changes, "conflicts": conflicts}
    if action == AnalysisRevision.ACTION_APPLY:
        subplans, conflicts = [], []
        created = after.get("created")
        if created is None:
            conflicts.append(
                {
                    "revision_id": entry["revision_id"],
                    "detail": "Apply antigo sem snapshot dos criados — não refeito.",
                }
            )
        else:
            subplans.append(
                {
                    "kind": "gate_recreate",
                    "changes": [
                        {"old_gate_id": int(gid), "snapshot": snap}
                        for gid, snap in created.items()
                    ],
                    "conflicts": [],
                }
            )
        replaced_changes = []
        for gid, snap in after.get("replaced", {}).items():
            gate = GateModel.objects.filter(pk=mid(gid)).first()
            if gate is None:
                conflicts.append(
                    {"gate_id": mid(gid), "detail": "Gate substituído não existe mais."}
                )
            else:
                replaced_changes.append(
                    {
                        "gate_id": gate.id,
                        "fields": {f: snap[f] for f in GATE_FIELDS if f in snap},
                    }
                )
        if replaced_changes:
            subplans.append(
                {
                    "kind": "gate_fields",
                    "changes": replaced_changes,
                    "conflicts": [],
                }
            )
        return {"kind": "chain", "plans": subplans, "conflicts": conflicts}
    if action == AnalysisRevision.ACTION_RESTORE:
        return {
            "kind": "none",
            "changes": [],
            "conflicts": [
                {
                    "revision_id": entry["revision_id"],
                    "detail": "Restauração aninhada não é refeita automaticamente.",
                }
            ],
        }
    # target_fields (disable/enable/move/rename de subsample): refaz o after.
    targets = after.get("targets", {})
    if targets:
        model = _target_model(target_type)
        changes, conflicts = [], []
        for tid, fields in targets.items():
            obj = model.objects.filter(pk=int(tid)).first()
            if obj is None:
                conflicts.append(
                    {"target_id": int(tid), "detail": "Registro não existe mais."}
                )
            else:
                changes.append({"obj": obj, "fields": fields})
        return {"kind": "target_fields", "changes": changes, "conflicts": conflicts}
    return {"kind": "none", "changes": [], "conflicts": []}


def state_at_revision(experiment, target_revision) -> dict:
    """Árvore de gates como estava na revisão-alvo — preview read-only (FE-25).

    Reconstrução virtual: parte do estado atual e aplica os inversos das
    revisões posteriores ao alvo, sem validar conflitos (o virtual sempre
    acompanha). `None` = estado inicial (sem gates).
    """
    virtual = {}
    for gate in GateModel.objects.filter(file_data__experiment=experiment):
        virtual[gate.id] = gate_snapshot(gate)
    for rev in _revisions_after(experiment, target_revision):
        _apply_inverse_virtual(virtual, rev)
    files = {}
    for gid, snap in sorted(virtual.items()):
        files.setdefault(str(snap["file_data_id"]), []).append({"id": gid, **snap})
    return {
        "revision_id": target_revision.id if target_revision else None,
        "files": files,
    }


def _apply_inverse_virtual(virtual: dict, rev: AnalysisRevision) -> None:
    action = rev.action
    before, after = rev.payload_before or {}, rev.payload_after or {}
    gate_field_actions = {
        AnalysisRevision.ACTION_RENAME,
        AnalysisRevision.ACTION_RECOLOR,
        AnalysisRevision.ACTION_UPDATE_GEOMETRY,
        AnalysisRevision.ACTION_REVERT,
    }
    if rev.target_type == AnalysisRevision.TARGET_GATE and action in gate_field_actions:
        for gid, fields in before.get("gates", {}).items():
            gate = virtual.get(int(gid))
            if gate is not None:
                gate.update(fields)
    elif action == AnalysisRevision.ACTION_CREATE and rev.target_type == "gate":
        for gid in after.get("gates", {}):
            virtual.pop(int(gid), None)
    elif action == AnalysisRevision.ACTION_DELETE and rev.target_type == "gate":
        for gid, snap in before.get("gates", {}).items():
            virtual[int(gid)] = snap
    elif action == AnalysisRevision.ACTION_APPLY:
        for gid in after.get("created_gate_ids", []):
            virtual.pop(int(gid), None)
        for gid, snap in before.get("replaced", {}).items():
            virtual[int(gid)] = snap
    elif action == AnalysisRevision.ACTION_RESTORE:
        # Desfazer um restore = refazer o que ele desfez (ordem cronológica).
        id_map = {
            int(old): new for old, new in after.get("recreated_id_map", {}).items()
        }
        for new_id in id_map.values():
            virtual.pop(new_id, None)
        for entry in sorted(after.get("undone", []), key=lambda e: e["revision_id"]):
            _apply_forward_virtual(virtual, entry, id_map)


def _apply_forward_virtual(virtual: dict, entry: dict, id_map: dict) -> None:
    """Refaz virtualmente o efeito de uma revisão desfeita por um restore."""
    action = entry["action"]
    before, after = entry.get("payload_before") or {}, entry.get("payload_after") or {}

    def mid(gid):
        return id_map.get(int(gid), int(gid))

    gate_field_actions = {
        AnalysisRevision.ACTION_RENAME,
        AnalysisRevision.ACTION_RECOLOR,
        AnalysisRevision.ACTION_UPDATE_GEOMETRY,
        AnalysisRevision.ACTION_REVERT,
    }
    if entry["target_type"] == "gate" and action in gate_field_actions:
        for gid, fields in after.get("gates", {}).items():
            gate = virtual.get(mid(gid))
            if gate is not None:
                gate.update(fields)
    elif action == AnalysisRevision.ACTION_CREATE:
        for gid, snap in after.get("gates", {}).items():
            virtual[int(gid)] = snap
    elif action == AnalysisRevision.ACTION_DELETE:
        for gid in before.get("gates", {}):
            virtual.pop(mid(gid), None)
    elif action == AnalysisRevision.ACTION_APPLY:
        for gid, snap in after.get("created", {}).items():
            virtual[int(gid)] = snap
        for gid, snap in after.get("replaced", {}).items():
            key = mid(gid)
            if key in virtual:
                virtual[key] = snap
    # restore aninhado e target_fields não afetam a árvore de gates do preview.
