"""Camada de revisão das sugestões do Juvia (BE-40).

O caminho direto do BE-27 (job → gates materializados no `complete`)
continua intacto pra quem enfileira por `/analysis-jobs/`. Quem pede por
`/experiment/<id>/suggestions/` cria um `SuggestionRun` ancorado no job —
o `complete` desse job grava `GateSuggestion` pendentes em vez de gates,
e gate só nasce no accept (juvia ADR-0003: o outcome aceita/ajustada/
descartada é o dataset de supervisão).
"""

from __future__ import annotations

from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework import serializers

from analytics.history import gate_snapshot, record_revision
from analytics.models import (
    AnalysisCheckpoint,
    AnalysisJob,
    AnalysisRevision,
    DashboardModel,
    GateModel,
    GateSuggestion,
    SuggestionRun,
)
from analytics.services.branches import ensure_main_branch
from analytics.services.jobs import JUVIA_DASHBOARD_NAME

# O Juvia roda os três modelos e fica com o melhor Calinski-Harabasz —
# "auto" é o model declarado do job de sugestão (JV-04 §2).
MODEL_AUTO = "auto"


def _suggestion_dashboard(file_data, parent) -> DashboardModel:
    """Dashboard que ancora o gate materializado: o do gate pai quando
    existe, senão o dashboard do serviço por amostra (mesma regra do
    caminho direto — a geometria carrega os eixos, o dashboard é só o
    ponto de exibição)."""
    if parent is not None and parent.dashboard_id:
        return parent.dashboard
    dashboard, _ = DashboardModel.objects.get_or_create(
        name=JUVIA_DASHBOARD_NAME,
        file_data=file_data,
        defaults={"dashboard_config": {}},
    )
    return dashboard


@transaction.atomic
def create_suggestion_run(
    *,
    experiment,
    file_data,
    parent,
    channels,
    gate_type,
    intent,
    control_files,
    user,
    branch=None,
    transform="linear",
    params=None,
) -> SuggestionRun:
    """Cria o run + job `pending` e aplica a regra "mesmo contexto
    substitui": candidatas ainda pendentes de runs anteriores no mesmo
    contexto (amostra + pai + branch + canais) viram `accepted=False`.

    Com gate pai a branch é sempre a dele (a hierarquia não atravessa
    branches); run de raiz usa a branch explícita ou a main."""
    if parent is not None:
        branch = parent.branch
    elif branch is None:
        branch = ensure_main_branch(experiment)
    stale_candidates = GateSuggestion.objects.filter(
        run__experiment=experiment,
        run__file_data=file_data,
        run__parent=parent,
        run__branch=branch,
        run__channels=channels,
        accepted__isnull=True,
    )
    stale_candidates.update(accepted=False)

    job = AnalysisJob.objects.create(
        experiment=experiment,
        file_data=file_data,
        gate=parent,
        model=MODEL_AUTO,
        params=params or {},
        transform=transform,
        channels=channels,
        payload={
            "gate_type": gate_type,
            "intent": intent,
            "control_files": control_files,
        },
        max_attempts=settings.JUVIA_JOB_MAX_ATTEMPTS,
        created_by=user,
    )
    return SuggestionRun.objects.create(
        experiment=experiment,
        file_data=file_data,
        parent=parent,
        branch=branch,
        job=job,
        channels=channels,
        gate_type=gate_type,
        intent=intent,
        control_files=control_files,
        experiment_type=experiment.type or "",
        created_by=user,
    )


def complete_suggestion_run(run: SuggestionRun, result_payload: dict) -> list:
    """Grava as candidatas do `complete` como pendentes — nunca gates.

    `candidates=[]` + `reason` fecha o run sem candidatas (feedback pro
    front, não erro). `parent_index` (índice dentro do próprio payload)
    resolve o aninhamento do Hierárquico quando o worker enviar.
    """
    candidates = result_payload.get("candidates") or []
    created = []
    for index, entry in enumerate(candidates):
        created.append(
            GateSuggestion.objects.create(
                run=run,
                name=(entry.get("name") or f"Candidata {index + 1}")[:50],
                gate_coordinates=entry.get("gate_coordinates") or {},
                stats=entry.get("stats") or {},
                sort_index=index,
            )
        )
    for index, entry in enumerate(candidates):
        parent_index = entry.get("parent_index")
        if isinstance(parent_index, int) and 0 <= parent_index < len(created):
            if parent_index != index:
                created[index].parent_candidate = created[parent_index]
                created[index].save(update_fields=["parent_candidate"])

    run.algorithm = result_payload.get("model") or ""
    run.params = result_payload.get("params") or {}
    run.metrics = result_payload.get("metrics") or {}
    run.reason = result_payload.get("reason") or ""
    run.save(update_fields=["algorithm", "params", "metrics", "reason"])
    return created


@transaction.atomic
def accept_suggestion(
    suggestion: GateSuggestion, *, user, name=None, color=None, gate_coordinates=None
) -> GateModel:
    """Materializa a candidata em `GateModel` + revisão + checkpoint.

    `automatic = (geometria intacta)` — aceite com `gate_coordinates`
    alteradas já nasce editada (`automatic=False`), mesmo sinal de uma
    consolidação posterior. A revisão fica com `origin=juvia`: a proposta
    é da máquina, o aceite do analista fica no `user`/`summary`.
    """
    # Sem select_related no lock: FOR UPDATE não pode travar o lado
    # nullable do outer join (parent_candidate/gate) — carrega lazy depois.
    suggestion = GateSuggestion.objects.select_for_update().get(pk=suggestion.pk)
    if suggestion.accepted is not None:
        raise serializers.ValidationError(
            {"detail": "Esta candidata já foi decidida (aceita ou descartada)."}
        )
    run = suggestion.run
    coordinates = (
        gate_coordinates
        if gate_coordinates is not None
        else suggestion.gate_coordinates
    )
    automatic = coordinates == suggestion.gate_coordinates

    # Candidata aninhada materializa sob o gate do pai — se a mãe ainda
    # não foi aceita, o filho cai no contexto do run (juvia ADR-0003:
    # nunca inventar hierarquia que o analista não confirmou).
    parent = run.parent
    if suggestion.parent_candidate_id and suggestion.parent_candidate.gate_id:
        parent = suggestion.parent_candidate.gate

    try:
        gate = GateModel.objects.create(
            file_data=run.file_data,
            name=(name or suggestion.name)[:50],
            gate_coordinates=coordinates,
            dashboard=_suggestion_dashboard(run.file_data, parent),
            parent=parent,
            branch=run.branch,
            color=color or None,
            automatic=automatic,
            created_by=user,
        )
    except IntegrityError as exc:
        raise serializers.ValidationError(
            {"detail": f"Nome de gate já em uso nesta amostra: {exc}"}
        )

    revision = record_revision(
        experiment=run.experiment,
        action=AnalysisRevision.ACTION_CREATE,
        target_type=AnalysisRevision.TARGET_GATE,
        target_id=gate.id,
        user=user,
        origin=AnalysisRevision.ORIGIN_JUVIA,
        payload_after={
            "gates": {str(gate.id): gate_snapshot(gate)},
            "suggestion_run_id": run.id,
            "suggestion_id": suggestion.id,
            "model": run.algorithm,
        },
        affected_ids=[gate.id],
        file_data=run.file_data,
        branch=run.branch,
        summary=(
            f"Sugestão do Juvia aceita: '{gate.name}' em "
            f"{run.file_data.file_name} (run #{run.id})"
        ),
    )
    AnalysisCheckpoint.objects.create(
        experiment=run.experiment,
        revision=revision,
        origin=AnalysisRevision.ORIGIN_JUVIA,
        message=f"Sugestão aceita ({run.algorithm or 'auto'}, run #{run.id})",
        created_by=user,
    )

    suggestion.accepted = True
    suggestion.gate = gate
    suggestion.save(update_fields=["accepted", "gate"])

    from analytics.tasks import recalculate_gate_analysis

    recalculate_gate_analysis(gate.id)
    return gate


def discard_suggestion(suggestion: GateSuggestion) -> GateSuggestion:
    """Marca a candidata como descartada — outcome do dataset (ADR-0003)."""
    suggestion = GateSuggestion.objects.select_for_update().get(pk=suggestion.pk)
    if suggestion.accepted is not None:
        raise serializers.ValidationError(
            {"detail": "Esta candidata já foi decidida (aceita ou descartada)."}
        )
    suggestion.accepted = False
    suggestion.save(update_fields=["accepted"])
    return suggestion
