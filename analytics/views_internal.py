"""Rotas /internal/* consumidas pelo Juvia (BE-27, juvia ADR-0002).

Serviço chama serviço: `InternalServiceView` exige o Bearer
`JUVIA_INTERNAL_TOKEN` (compare_digest, fail-closed sem env). Essas rotas
não aceitam credencial de usuário e não passam por queryset escopada de
usuário — o escopo é resolvido explicitamente por job/amostra. O nginx
não deve publicar `/internal/` (pandora-nginx); a auth aqui é a defesa
em profundidade, não o perímetro.
"""

import logging

from django.conf import settings
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response

from analytics.models import AnalysisJob, GateModel
from analytics.serializers import (
    AnalysisJobClaimSerializer,
    JobClaimRequestSerializer,
    JobCompleteSerializer,
    JobFailSerializer,
)
from analytics.services.jobs import (
    claim_next_job,
    complete_job,
    fail_job,
    sample_file_events,
)
from fcs_parser.models import FileDataModel
from utils.internal_auth import InternalServiceView

logger = logging.getLogger(__name__)

EVENTS_DEFAULT_N = 10000


def _internal_job(job_id) -> AnalysisJob:
    return get_object_or_404(AnalysisJob, pk=job_id)


class InternalJobClaimView(InternalServiceView):
    """POST /internal/jobs/claim — claim atômico do próximo job.

    204 quando a fila está vazia; 200 com o job + referências de dados
    (`file_id`, `gate_id`, `subsample_id`) e o contrato do worker
    (`model`/`params`/`transform`/`channels` — espelha o ClusterRequest).
    """

    @extend_schema(
        request=JobClaimRequestSerializer,
        responses={200: AnalysisJobClaimSerializer, 204: None},
        tags=["internal"],
    )
    def post(self, request):
        serializer = JobClaimRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        job = claim_next_job(worker=serializer.validated_data["worker"])
        if job is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(AnalysisJobClaimSerializer(job).data)


class InternalFileEventsView(InternalServiceView):
    """GET /internal/files/{id}/events?gate=<id>&n=<int>&channels=a,b

    Resolve o contexto (amostra ativa, compensação, cadeia do gate) e
    devolve os eventos com amostragem de seed fixa — sem expor storage
    nem ids internos além dos já recebidos.
    """

    @extend_schema(
        parameters=[
            OpenApiParameter("gate", OpenApiTypes.INT, required=False),
            OpenApiParameter("n", OpenApiTypes.INT, required=False),
            OpenApiParameter("channels", OpenApiTypes.STR, required=False),
        ],
        tags=["internal"],
    )
    def get(self, request, file_id):
        file_data = get_object_or_404(
            FileDataModel,
            pk=file_id,
            active=True,
            experiment__active=True,
        )
        try:
            n = min(
                max(int(request.query_params.get("n", EVENTS_DEFAULT_N)), 1),
                settings.JUVIA_EVENTS_MAX_SAMPLE,
            )
        except (TypeError, ValueError):
            n = EVENTS_DEFAULT_N

        gate = None
        gate_id = request.query_params.get("gate")
        if gate_id:
            if not str(gate_id).isdigit():
                return Response(
                    {"detail": "gate deve ser um id inteiro."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            gate = get_object_or_404(
                GateModel.objects.select_related("dashboard", "parent"),
                pk=gate_id,
            )

        channels = None
        raw_channels = request.query_params.get("channels")
        if raw_channels:
            channels = [c.strip() for c in raw_channels.split(",") if c.strip()]

        try:
            payload = sample_file_events(file_data, gate=gate, n=n, channels=channels)
        except serializers.ValidationError as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                # DRF embrulha cada valor em lista — desembrulha escalares
                # pra manter o contrato {detail: "...", ...} flat.
                detail = {
                    k: v[0] if isinstance(v, list) and len(v) == 1 else v
                    for k, v in detail.items()
                }
            return Response(detail, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "file_id": file_data.id,
                "gate_id": gate.id if gate else None,
                "subsample_id": file_data.subsample_id,
                **payload,
            }
        )


class InternalJobCompleteView(InternalServiceView):
    """POST /internal/jobs/{id}/complete — materializa o resultado.

    `result.gates[]` vira gates automáticas + revisão `origin=juvia` +
    checkpoint na mesma transação; o resto do payload é gravado opaco.
    Re-complete de job já `done` devolve os artefatos existentes (200),
    sem recriar gates — o worker pode ter perdido a resposta.
    """

    @extend_schema(
        request=JobCompleteSerializer,
        responses={200: None},
        tags=["internal"],
    )
    def post(self, request, job_id):
        serializer = JobCompleteSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        job = _internal_job(job_id)
        complete_job(job, request.data or {})
        job.refresh_from_db()
        response = {
            "job_id": job.id,
            "status": job.status,
            "created_gate_ids": (job.result or {}).get("created_gate_ids", []),
            "revision_id": (job.result or {}).get("revision_id"),
            "checkpoint_id": (job.result or {}).get("checkpoint_id"),
        }
        return Response(response, status=status.HTTP_200_OK)


class InternalJobFailView(InternalServiceView):
    """POST /internal/jobs/{id}/fail — falha com retentativa/quarentena."""

    @extend_schema(
        request=JobFailSerializer,
        responses={200: None},
        tags=["internal"],
    )
    def post(self, request, job_id):
        serializer = JobFailSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        job = fail_job(_internal_job(job_id), serializer.validated_data["error"])
        return Response(
            {
                "job_id": job.id,
                "status": job.status,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
            }
        )
