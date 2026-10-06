"""Ciclo de vida dos jobs de análise consumidos pelo Juvia (BE-27).

A fila mora aqui (juvia ADR-0002 — o Juvia é stateless e faz poll): claim
atômico, resolução do contexto de dados, materialização do resultado
(gates + revisão + checkpoint com origem `juvia`) e retentativa até
quarentena. As views internas ficam magras delegando pra cá.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework import serializers

from analytics.gate_filter import apply_gate_filter, missing_gate_channels
from analytics.history import gate_snapshot, record_revision
from analytics.models import (
    AnalysisCheckpoint,
    AnalysisJob,
    AnalysisRevision,
    DashboardModel,
    GateModel,
)
from fcs_parser.services.compensation import (
    applied_compensation,
    apply_compensation,
)
from utils.density import normalize_column_name, normalize_columns
from utils.internal_auth import Conflict

logger = logging.getLogger(__name__)

# Seed fixa da amostragem — o mesmo recorte devolve os mesmos eventos para
# qualquer worker (payload uniforme, BE-27).
EVENTS_RANDOM_STATE = 42

# Dashboard que ancora as gates sugeridas quando o job não tem gate pai
# pra herdar — o gate_coordinates carrega os eixos, então o dashboard é
# só o ponto de exibição (ADR-0003 do Juvia, geometria transferível).
JUVIA_DASHBOARD_NAME = "Juvia"

_LAST_ERROR_MAX_LEN = 4000


def claim_next_job(worker: str = "") -> AnalysisJob | None:
    """Claim atômico do próximo job elegível via FOR UPDATE SKIP LOCKED.

    Elegível: `pending`; `error` ainda dentro do teto de retentativas
    (o claim seguinte é a retentativa — juvia ADR-0002); ou `processing`
    com o claim expirado (worker que morreu não trava a fila). N workers
    concorrentes nunca pegam o mesmo job — o SKIP LOCKED pula a linha que
    outra transação já trancou.
    """
    now = timezone.now()
    lease_before = now - timedelta(minutes=settings.JUVIA_JOB_CLAIM_TIMEOUT_MINUTES)
    with transaction.atomic():
        job = (
            AnalysisJob.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status=AnalysisJob.STATUS_PENDING)
                | Q(
                    status=AnalysisJob.STATUS_ERROR,
                    attempts__lt=F("max_attempts"),
                )
                | Q(
                    status=AnalysisJob.STATUS_PROCESSING,
                    claimed_at__lt=lease_before,
                )
            )
            .order_by("created_at", "id")
            .first()
        )
        if job is None:
            return None
        job.status = AnalysisJob.STATUS_PROCESSING
        job.claimed_at = now
        job.claimed_by = worker
        job.save(update_fields=["status", "claimed_at", "claimed_by"])
    return job


def sample_file_events(file_data, *, gate=None, n: int = 10000, channels=None) -> dict:
    """Eventos da amostra (opcionalmente recortados por um gate) pro worker.

    Espelha o recorte que o analista vê no produto: colunas normalizadas,
    compensação aplicada quando houver (BE-22) e a cadeia inteira de gates
    do root até o alvo — nunca o parquet cru nem caminho de storage.
    """
    dataset = normalize_columns(file_data.get_dataframe())
    if dataset.empty:
        raise serializers.ValidationError(
            {"detail": "A amostra não tem eventos disponíveis."}
        )

    applied = applied_compensation(file_data.experiment)
    if applied:
        dataset = apply_compensation(dataset, applied.channels, applied.matrix)

    if gate is not None:
        if gate.file_data_id != file_data.id:
            raise serializers.ValidationError(
                {"detail": "O gate não pertence a esta amostra."}
            )
        gate_path = []
        current = gate
        while current is not None:
            gate_path.insert(0, current)
            current = current.parent
        columns = set(dataset.columns)
        for gate_in_path in gate_path:
            missing = missing_gate_channels(gate_in_path, columns)
            if missing:
                raise serializers.ValidationError(
                    {
                        "detail": (
                            f"O gate '{gate_in_path.name}' referencia o(s) "
                            f"canal(is) {', '.join(missing)}, ausente(s) "
                            "nesta amostra."
                        ),
                        "missing_channels": missing,
                        "gate_id": gate_in_path.id,
                    }
                )
            dataset = apply_gate_filter(dataset, gate_in_path)
            if dataset.empty:
                break

    if channels:
        wanted = [normalize_column_name(c) for c in channels]
        missing_out = [c for c in wanted if c not in dataset.columns]
        if missing_out:
            raise serializers.ValidationError(
                {"detail": ("Canais ausentes nesta amostra: " + ", ".join(missing_out))}
            )
        dataset = dataset[wanted]

    n_total = len(dataset)
    if n_total > n:
        dataset = dataset.sample(n=n, random_state=EVENTS_RANDOM_STATE)

    return {
        "channels": list(dataset.columns),
        "n_total": n_total,
        "events": dataset.values.tolist(),
    }


def _resolve_result_dashboard(job) -> DashboardModel:
    """Dashboard que ancora as gates sugeridas pelo worker.

    Com gate de contexto o dashboard dele é herdado (as filhas aparecem
    na mesma projeção do pai); sem, um dashboard próprio do serviço por
    amostra — o `gate_coordinates` do resultado carrega os eixos reais,
    então o dashboard é só o ponto de exibição.
    """
    if job.gate_id and job.gate.dashboard_id:
        return job.gate.dashboard
    dashboard, _ = DashboardModel.objects.get_or_create(
        name=JUVIA_DASHBOARD_NAME,
        file_data=job.file_data,
        defaults={"dashboard_config": {}},
    )
    return dashboard


def _result_gate_parent(job, entry: dict):
    """Pai do gate sugerido: `parent_id` explícito do resultado ou o gate
    de contexto do job. Sempre dentro da mesma amostra."""
    parent_id = entry.get("parent_id")
    if parent_id is None:
        return job.gate
    parent = GateModel.objects.filter(
        pk=parent_id, file_data_id=job.file_data_id
    ).first()
    if parent is None:
        raise serializers.ValidationError(
            {"detail": (f"Gate pai {parent_id} não existe nesta amostra.")}
        )
    return parent


@transaction.atomic
def complete_job(job: AnalysisJob, result_payload: dict) -> dict:
    """Materializa o resultado do worker: gates + revisão + checkpoint.

    Tudo numa transação com lock na linha do job — `done` só se os
    artefatos gravaram e dois completes concorrentes não duplicam gates.
    Idempotente: re-complete de um job já `done` devolve os artefatos
    gravados sem recriar nada (o worker pode ter perdido a resposta).
    """
    # Sem select_related aqui: FOR UPDATE não pode travar o lado nullable
    # do outer join (gate/subsample) — os FKs carregam lazy depois.
    job = AnalysisJob.objects.select_for_update().get(pk=job.pk)
    if job.status == AnalysisJob.STATUS_DONE:
        return {"already_done": True}
    if job.status != AnalysisJob.STATUS_PROCESSING:
        raise Conflict(f"Job {job.id} não está em processamento (status={job.status}).")

    gates_payload = result_payload.get("gates") or []
    created = []
    if gates_payload:
        dashboard = _resolve_result_dashboard(job)
        try:
            for entry in gates_payload:
                created.append(
                    GateModel.objects.create(
                        file_data=job.file_data,
                        name=entry["name"],
                        gate_coordinates=entry["gate_coordinates"],
                        dashboard=dashboard,
                        parent=_result_gate_parent(job, entry),
                        color=entry.get("color") or None,
                        automatic=True,
                        created_by=None,
                    )
                )
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {"detail": f"Nome de gate já em uso nesta amostra: {exc}"}
            )

        revision = record_revision(
            experiment=job.experiment,
            action=AnalysisRevision.ACTION_CREATE,
            target_type=AnalysisRevision.TARGET_GATE,
            target_id=created[0].id,
            user=None,
            origin=AnalysisRevision.ORIGIN_JUVIA,
            payload_after={
                "gates": {str(g.id): gate_snapshot(g) for g in created},
                "job_id": job.id,
                "model": job.model,
                "params": job.params,
            },
            affected_ids=[g.id for g in created],
            file_data=job.file_data,
            branch=created[0].branch,
            summary=(
                f"Juvia sugeriu {len(created)} "
                f"população(ões) em {job.file_data.file_name} "
                f"(job #{job.id})"
            ),
        )
        checkpoint = AnalysisCheckpoint.objects.create(
            experiment=job.experiment,
            revision=revision,
            origin=AnalysisRevision.ORIGIN_JUVIA,
            message=f"Sugestões do Juvia ({job.model}, job #{job.id})",
            created_by=job.created_by,
        )
        job.result = {
            "worker_result": result_payload,
            "created_gate_ids": [g.id for g in created],
            "revision_id": revision.id,
            "checkpoint_id": checkpoint.id,
        }
        # Estatísticas das populações sugeridas — mesmo recálculo da
        # criação manual (o front lê count/%/MFI do AnalysisResult).
        from analytics.tasks import recalculate_gate_analysis

        for gate in created:
            recalculate_gate_analysis(gate.id)
    else:
        # Complete sem gates = worker concluiu sem sugestões — ainda é um
        # desfecho válido; nada entra no histórico de análise.
        job.result = {"worker_result": result_payload, "created_gate_ids": []}

    job.status = AnalysisJob.STATUS_DONE
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at", "result"])
    return {"already_done": False}


@transaction.atomic
def fail_job(job: AnalysisJob, error: str) -> AnalysisJob:
    """Registra a falha: retenta enquanto couber, senão quarentena.

    `quarantine` é terminal de revisão manual — o job nunca é descartado
    nem reexecutado sozinho (BE-27, juvia ADR-0002).
    """
    locked = AnalysisJob.objects.select_for_update().get(pk=job.pk)
    if locked.status != AnalysisJob.STATUS_PROCESSING:
        raise Conflict(
            f"Job {locked.id} não está em processamento " f"(status={locked.status})."
        )
    locked.attempts += 1
    locked.last_error = (error or "")[:_LAST_ERROR_MAX_LEN]
    if locked.attempts >= locked.max_attempts:
        locked.status = AnalysisJob.STATUS_QUARANTINE
        locked.finished_at = timezone.now()
        locked.save(
            update_fields=[
                "attempts",
                "last_error",
                "status",
                "finished_at",
            ]
        )
        logger.warning(
            "AnalysisJob %s em quarentena após %s tentativas: %s",
            locked.id,
            locked.attempts,
            locked.last_error[:200],
        )
    else:
        locked.status = AnalysisJob.STATUS_ERROR
        locked.save(update_fields=["attempts", "last_error", "status"])
    return locked
