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
    AnalysisRevision.ACTION_REVERT,
}

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
) -> AnalysisRevision:
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


def _gate_field_conflicts(revision: AnalysisRevision) -> tuple[list, list]:
    """Confere cada alvo de uma edição de campos (rename/recolor/geometry).

    Retorna (mudanças, conflitos): para cada gate registrado, exige que ele
    exista e que os campos tocados continuem com o valor `after` — se o
    gate foi alterado depois, a reversão é bloqueada em vez de sobrescrever
    às cegas.
    """
    changes = []
    conflicts = []
    before = revision.payload_before.get("gates", {})
    after = revision.payload_after.get("gates", {})
    for gid, before_fields in before.items():
        gate = GateModel.objects.filter(pk=int(gid)).first()
        if gate is None:
            conflicts.append({"gate_id": int(gid), "detail": "Gate não existe mais."})
            continue
        after_fields = after.get(gid, {})
        drifted = [
            field
            for field, value in after_fields.items()
            if getattr(gate, field) != value
        ]
        if drifted:
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


def _plan_gate_update_revert(revision: AnalysisRevision):
    changes, conflicts = _gate_field_conflicts(revision)
    return {
        "kind": "gate_fields",
        "changes": changes,
        "conflicts": conflicts,
    }


def _plan_gate_create_revert(revision: AnalysisRevision):
    """Reverter uma criação = excluir o gate criado."""
    created = revision.payload_after.get("gates", {})
    changes = []
    conflicts = []
    for gid in created:
        gate = GateModel.objects.filter(pk=int(gid)).first()
        if gate is None:
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


def _plan_gate_delete_revert(revision: AnalysisRevision):
    """Reverter uma exclusão = recriar as linhas do snapshot (pais primeiro)."""
    snapshots = revision.payload_before.get("gates", {})
    changes = []
    conflicts = []
    for gid, snap in snapshots.items():
        parent_id = snap.get("parent_id")
        parent_known = parent_id is None or str(parent_id) in snapshots
        parent_alive = (
            parent_id is None or GateModel.objects.filter(pk=parent_id).exists()
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


def _plan_apply_revert(revision: AnalysisRevision):
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
            conflicts.append(
                {"gate_id": int(gid), "detail": "Gate substituído não existe mais."}
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


def _plan_target_fields_revert(revision: AnalysisRevision):
    """Reversão genérica de campos em amostra/subsample/experimento.

    Cobre disable/enable (campo `active`), move_subsample (`subsample_id`)
    e rename de subsample (`name`): exige que os campos tocados continuem
    com o valor `after` — drift bloqueia a reversão.
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
        if drifted:
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


def plan_revert(revision: AnalysisRevision) -> dict:
    """O que a reversão faria — e o que a bloqueia — sem gravar nada."""
    if revision.action not in REVERSIBLE_ACTIONS:
        return {"kind": "none", "changes": [], "conflicts": []}
    if revision.action in (
        AnalysisRevision.ACTION_RENAME,
        AnalysisRevision.ACTION_RECOLOR,
        AnalysisRevision.ACTION_UPDATE_GEOMETRY,
        AnalysisRevision.ACTION_REVERT,
    ):
        if revision.target_type == AnalysisRevision.TARGET_GATE:
            return _plan_gate_update_revert(revision)
        return _plan_target_fields_revert(revision)
    if revision.action == AnalysisRevision.ACTION_DELETE:
        if revision.target_type == AnalysisRevision.TARGET_GATE:
            return _plan_gate_delete_revert(revision)
        return _plan_target_fields_revert(revision)
    if revision.action == AnalysisRevision.ACTION_CREATE:
        if revision.target_type == AnalysisRevision.TARGET_GATE:
            return _plan_gate_create_revert(revision)
        # Subsample criado pela UI: reverter é inativá-lo de novo.
        return _plan_create_inactivate_revert(revision)
    if revision.action == AnalysisRevision.ACTION_APPLY:
        return _plan_apply_revert(revision)
    if revision.action in (
        AnalysisRevision.ACTION_DISABLE,
        AnalysisRevision.ACTION_ENABLE,
        AnalysisRevision.ACTION_MOVE_SUBSAMPLE,
    ):
        return _plan_target_fields_revert(revision)
    return {"kind": "none", "changes": [], "conflicts": []}


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


def _apply_plan(revision: AnalysisRevision, plan: dict) -> dict:
    """Executa o plano calculado por `plan_revert`. Retorna o que mudou."""
    kind = plan["kind"]
    applied = []

    if kind == "gate_fields":
        for change in plan["changes"]:
            gate = GateModel.objects.get(pk=change["gate_id"])
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
            GateModel.objects.filter(pk=change["gate_id"]).delete()
            applied.append({"gate_id": change["gate_id"], "deleted": True})

    elif kind == "gate_recreate":
        id_map = {}
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
            applied.append({"old_gate_id": old_id, "gate_id": new_gate.id})

    elif kind == "apply":
        for change in plan["changes"]:
            if change["op"] == "delete":
                GateModel.objects.filter(pk=change["gate_id"]).delete()
            else:
                gate = GateModel.objects.get(pk=change["gate_id"])
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

    return {"applied": applied}


def public_changes(plan: dict) -> list:
    """`plan["changes"]` sem objetos de modelo — versão segura p/ resposta."""
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
        "would_change": plan["changes"],
        "applied": result["applied"],
    }
