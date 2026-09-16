"""Derivação de estratégia de análise entre experimentos (BE-19, ADR-0021).

`derive_analysis(target, source, ...)` copia a árvore de gates das amostras
da origem para as amostras casadas do alvo — match por `content_guid`
(identidade de conteúdo, ADR-0012) com fallback por `file_name`. É snapshot:
sem vínculo vivo e sem `copied_from` cross-experimento (a família de cópias
é intra-experimento, ADR-0003) — a proveniência fica nas revisões.

Por amostra casada grava-se uma revisão `create` (revertível: apaga os
gates criados); a derivação inteira é ancorada por uma revisão `derive`
no experimento alvo.
"""

from __future__ import annotations

from django.db import transaction

from analytics.history import gate_snapshot, record_revision
from analytics.models import (
    AnalysisResult,
    AnalysisRevision,
    CompensationMatrix,
    DashboardModel,
    GateModel,
)
from fcs_parser.models import FileDataModel, SubsampleModel


def _match_files(source_files, target_files):
    """Casa amostras origem→alvo: content_guid, depois file_name.

    Devolve (matches, unmatched_source, unmatched_target) — matches é uma
    lista de pares (source_fd, target_fd).
    """
    by_guid: dict[str, FileDataModel] = {}
    by_name: dict[str, FileDataModel] = {}
    for fd in target_files:
        if fd.content_guid:
            by_guid.setdefault(fd.content_guid, fd)
        by_name.setdefault(fd.file_name, fd)

    matches: list[tuple[FileDataModel, FileDataModel]] = []
    matched_target_ids: set[int] = set()
    unmatched_source: list[FileDataModel] = []
    for sfd in source_files:
        tfd = None
        if sfd.content_guid and sfd.content_guid in by_guid:
            tfd = by_guid[sfd.content_guid]
        elif sfd.file_name in by_name:
            tfd = by_name[sfd.file_name]
        if tfd is None or tfd.id in matched_target_ids:
            unmatched_source.append(sfd)
            continue
        matches.append((sfd, tfd))
        matched_target_ids.add(tfd.id)
    unmatched_target = [f for f in target_files if f.id not in matched_target_ids]
    return matches, unmatched_source, unmatched_target


def _target_dashboard(fd: FileDataModel) -> DashboardModel:
    dash = DashboardModel.objects.filter(file_data=fd).first()
    if dash is None:
        dash = DashboardModel.objects.create(
            file_data=fd,
            name="default",
            dashboard_config={"x_axis_label": "FSC-A", "y_axis_label": "SSC-A"},
        )
    return dash


def _copy_gate_tree(
    source_fd: FileDataModel, target_fd: FileDataModel, user, branch=None
):
    """Clona a árvore de gates de source_fd em target_fd (sem copied_from —
    cross-experimento não entra na família de cópias, ADR-0021).

    Devolve (gate_map, snapshots) — snapshots no formato do payload de
    revisão `create` ({id: gate_snapshot}).
    """
    dashboard = _target_dashboard(target_fd)
    gate_map: dict[int, GateModel] = {}
    snapshots: dict[str, dict] = {}
    pending = list(GateModel.objects.filter(file_data=source_fd).order_by("created_at"))
    while pending:
        progressed = False
        for gate in pending[:]:
            if gate.parent_id is not None and gate.parent_id not in gate_map:
                continue
            new_gate = GateModel.objects.create(
                file_data=target_fd,
                name=gate.name,
                gate_coordinates=gate.gate_coordinates,
                plot_config=gate.plot_config,
                dashboard=dashboard,
                parent=gate_map.get(gate.parent_id),
                copied_from=None,
                branch=branch,
                color=gate.color,
                created_by=user,
            )
            gate_map[gate.id] = new_gate
            snapshots[str(new_gate.id)] = gate_snapshot(new_gate)
            pending.remove(gate)
            progressed = True
        if not progressed:
            break  # ciclo ou pai fora do conjunto — não deveria acontecer
    return gate_map, snapshots


def derive_analysis(
    target,
    source,
    *,
    user,
    include_subsamples: bool = True,
    include_compensation: bool = True,
) -> dict:
    """Deriva a estratégia de `source` em `target` (ambos ExperimentModel).

    O chamador valida permissões (can_edit nos dois). Devolve o relatório
    da derivação para a resposta da API.
    """
    from fcs_parser.services.compensation import set_applied_compensation

    from analytics.services.branches import ensure_main_branch

    # BE-19 derivado vai para a main do alvo — criar a análise recebida
    # como branch separada é a evolução quando BE-23 tiver UI.
    target_main = ensure_main_branch(target)

    source_files = list(FileDataModel.objects.filter(experiment=source, active=True))
    target_files = list(FileDataModel.objects.filter(experiment=target, active=True))
    matches, unmatched_source, unmatched_target = _match_files(
        source_files, target_files
    )

    report = {
        "source_experiment_id": source.id,
        "matched_files": 0,
        "created_gates": 0,
        "skipped_files": [],
        "unmatched_source_files": [
            {"id": f.id, "file_name": f.file_name} for f in unmatched_source
        ],
        "unmatched_target_files": [
            {"id": f.id, "file_name": f.file_name} for f in unmatched_target
        ],
        "subsamples_created": [],
        "compensation_applied": False,
    }

    with transaction.atomic():
        subsample_by_name = {
            s.name: s
            for s in SubsampleModel.objects.filter(experiment=target, active=True)
        }

        for sfd, tfd in matches:
            existing = GateModel.objects.filter(file_data=tfd).count()
            if existing:
                report["skipped_files"].append(
                    {
                        "id": tfd.id,
                        "file_name": tfd.file_name,
                        "reason": "a amostra já possui gates",
                    }
                )
                continue

            gate_map, snapshots = _copy_gate_tree(sfd, tfd, user, branch=target_main)
            if not gate_map:
                report["skipped_files"].append(
                    {
                        "id": tfd.id,
                        "file_name": tfd.file_name,
                        "reason": "a amostra de origem não possui gates",
                    }
                )
                continue

            record_revision(
                experiment=target,
                action=AnalysisRevision.ACTION_CREATE,
                target_type=AnalysisRevision.TARGET_GATE,
                target_id=next(iter(gate_map.values())).id,
                user=user,
                scope="file",
                payload_after={"gates": snapshots},
                affected_ids=[g.id for g in gate_map.values()],
                summary=(
                    f"derivou {len(gate_map)} "
                    f'{"gate" if len(gate_map) == 1 else "gates"} do '
                    f'experimento "{source.title}"'
                ),
            )
            report["matched_files"] += 1
            report["created_gates"] += len(gate_map)

            # Encaixe no subsample de mesmo nome da origem (BE-19).
            if include_subsamples and sfd.subsample_id:
                source_sub = sfd.subsample
                tsub = subsample_by_name.get(source_sub.name)
                if tsub is None:
                    tsub = SubsampleModel.objects.create(
                        experiment=target,
                        name=source_sub.name,
                        source_path=source_sub.source_path,
                        active=True,
                        created_by=user,
                    )
                    subsample_by_name[tsub.name] = tsub
                    report["subsamples_created"].append(tsub.name)
                if tfd.subsample_id != tsub.id:
                    old_id = tfd.subsample_id
                    tfd.subsample = tsub
                    tfd.save(update_fields=["subsample"])
                    record_revision(
                        experiment=target,
                        action=AnalysisRevision.ACTION_MOVE_SUBSAMPLE,
                        target_type=AnalysisRevision.TARGET_FILE,
                        target_id=tfd.id,
                        user=user,
                        payload_before={
                            "targets": {str(tfd.id): {"subsample_id": old_id}}
                        },
                        payload_after={
                            "targets": {str(tfd.id): {"subsample_id": tsub.id}}
                        },
                        affected_ids=[tfd.id],
                        summary=(
                            f'moveu a amostra "{tfd.file_name}" para ' f'"{tsub.name}"'
                        ),
                    )

        # Compensação aplicada na origem acompanha a derivação (BE-22).
        if include_compensation:
            applied = CompensationMatrix.objects.filter(
                experiment=source, is_applied=True, active=True
            ).first()
            if applied is not None:
                clone = CompensationMatrix.objects.create(
                    experiment=target,
                    name=applied.name,
                    channels=applied.channels,
                    matrix=applied.matrix,
                    source=applied.source,
                    created_by=user,
                )
                set_applied_compensation(target, clone, user)
                report["compensation_applied"] = True

        if report["matched_files"] or report["created_gates"]:
            record_revision(
                experiment=target,
                action=AnalysisRevision.ACTION_DERIVE,
                target_type=AnalysisRevision.TARGET_EXPERIMENT,
                target_id=target.id,
                user=user,
                scope="experiment",
                payload_after={
                    "source_experiment_id": source.id,
                    "source_title": source.title,
                    "matched_files": report["matched_files"],
                    "created_gates": report["created_gates"],
                },
                summary=(
                    f'derivou a análise do experimento "{source.title}" — '
                    f'{report["matched_files"]} '
                    f'{"amostra" if report["matched_files"] == 1 else "amostras"}, '
                    f'{report["created_gates"]} '
                    f'{"gate" if report["created_gates"] == 1 else "gates"}'
                ),
            )

    return report
