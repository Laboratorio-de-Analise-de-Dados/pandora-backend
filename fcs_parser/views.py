import json
import logging
import os
import tempfile
import traceback
import zipfile

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import (
    Case,
    CharField,
    Exists,
    OuterRef,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Lower
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from accounts.models import Membership, Organization
from analytics.history import record_revision
from analytics.models import AnalysisRevision, GateModel
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiParameter
from rest_framework.response import Response
from rest_framework import status
from rest_framework import generics, serializers
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from utils.density import (
    DEFAULT_COFACTOR,
    compute_density,
    compute_histogram,
    default_scale,
    density_cache_key,
    get_cached_density,
    invalidate_density,
    normalize_columns,
    parse_range,
    set_cached_density,
    subsample_scatter,
)
from fcs_parser.models import (
    ExperimentModel,
    ExperimentTypeModel,
    FileDataModel,
    FileTagModel,
    FileModel,
    SampleTagModel,
    SubsampleModel,
)
from fcs_parser.permissions import (
    can_create_experiment_type,
    can_edit_experiment,
    can_move_experiment,
    experiments_visible_to,
    file_data_visible_to,
    is_org_member,
    require_can_edit_experiment,
    require_can_edit_file_data,
    uploads_visible_to,
)
from analytics.serializers import CompensationPreviewSerializer
from fcs_parser.services.compensation import (
    applied_compensation,
    apply_compensation,
)
from fcs_parser.services.copy_experiment import copy_experiment
from fcs_parser.services.derive_analysis import derive_analysis
from fcs_parser.serializers import (
    ChunkUploadSerializer,
    ExperimentCompleteSerializer,
    ExperimentCreateSerializer,
    ExperimentFileInitSerializer,
    ExperimentInitSerializer,
    ExperimentTypeSerializer,
    FilePlotConfigSerializer,
    FileTagsUpdateSerializer,
    ListExperimentSerializer,
    ListFileDataSerializer,
    ParamListDataSerializer,
    SampleTagSerializer,
    SubsampleSerializer,
    UpdateExperimentSerializer,
)
from fcs_parser.services.process_experiment_file import (
    assemble_chunks,
    extract_metadata_from_zip,
    file_sha256,
    wrap_fcs_as_zip,
)
from utils.validators import experiment_file_extension

logger = logging.getLogger(__name__)

INACTIVE_FILE_DETAIL = "Amostra desabilitada. Reative-a para continuar a análise."


def _first_error(errors) -> str:
    """Achata ``serializer.errors`` na primeira mensagem.

    Preserva o contrato ``{"detail": "<mensagem>"}`` dos endpoints antigos
    agora que a validação mora no serializer (ADR-0009).
    """
    if isinstance(errors, dict):
        return _first_error(next(iter(errors.values())))
    if isinstance(errors, (list, tuple)):
        return _first_error(errors[0])
    return str(errors)


def _invalid(serializer) -> Response:
    return Response(
        {"detail": _first_error(serializer.errors)},
        status=status.HTTP_400_BAD_REQUEST,
    )


def get_active_file_data_or_error(user, file_id):
    """Busca um FileData ativo e visível. (file_data, None) ou (None, Response)."""
    file_data = get_object_or_404(file_data_visible_to(user), id=file_id)
    if not file_data.active:
        return None, Response(
            {"detail": INACTIVE_FILE_DETAIL}, status=status.HTTP_409_CONFLICT
        )
    return file_data, None


class ExperimentInitView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ExperimentInitSerializer,
        responses=inline_serializer(
            name="ExperimentInitResponse",
            fields={"fileId": serializers.CharField()},
        ),
    )
    def post(self, request):
        serializer = ExperimentInitSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return _invalid(serializer)

        data = serializer.validated_data
        try:
            experiment = ExperimentModel.objects.create(
                title=data["title"],
                type=data["type"],
                description=data.get("description", ""),
                status="uploading",
                file_status="uploading",
                total_chunks=data["totalChunks"],
                organization_id=data.get("organizationId"),
                created_by=request.user,
            )
        except IntegrityError:
            return Response(
                {"detail": "Título já criado para esse laboratório."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"fileId": str(experiment.id)}, status=201)


class UploadChunkView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ChunkUploadSerializer,
        responses=inline_serializer(
            name="StatusResponse",
            fields={"status": serializers.CharField()},
        ),
    )
    def post(self, request):
        serializer = ChunkUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return _invalid(serializer)

        file_id = serializer.validated_data["fileId"]
        chunk_index = serializer.validated_data["chunkIndex"]
        chunk = serializer.validated_data["chunk"]

        experiment = get_object_or_404(experiments_visible_to(request.user), id=file_id)
        require_can_edit_experiment(request.user, experiment)

        chunk_dir = os.path.join(settings.MEDIA_ROOT, "chunks")
        os.makedirs(chunk_dir, exist_ok=True)
        chunk_path = os.path.join(chunk_dir, f"{file_id}_{chunk_index}.part")
        with open(chunk_path, "wb") as f:
            for c in chunk.chunks():
                f.write(c)

        if chunk_index not in experiment.received_chunks:
            experiment.received_chunks.append(chunk_index)
            experiment.save(update_fields=["received_chunks"])

        return Response({"status": "ok"})


class ExperimentCompleteView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ExperimentCompleteSerializer,
        responses=inline_serializer(
            name="ExperimentCompleteResponse",
            fields={"status": serializers.CharField()},
        ),
    )
    def post(self, request):
        serializer = ExperimentCompleteSerializer(data=request.data)
        if not serializer.is_valid():
            return _invalid(serializer)

        file_id = serializer.validated_data["fileId"]
        raw_file_name = serializer.validated_data.get("fileName")
        # `fileName` é opcional para não quebrar clientes antigos, que só
        # enviavam ZIP.
        extension = (
            experiment_file_extension(raw_file_name)
            if raw_file_name not in (None, "")
            else ".zip"
        )

        experiment = get_object_or_404(experiments_visible_to(request.user), id=file_id)
        require_can_edit_experiment(request.user, experiment)

        final_path = assemble_chunks(
            str(experiment.id), experiment.total_chunks, extension
        )
        final_name = f"{file_id}{extension}"

        # `.fcs` solto é aglutinado num ZIP — a unidade física é sempre ZIP.
        if extension == ".fcs":
            zip_path = os.path.join(settings.MEDIA_ROOT, f"{file_id}.zip")
            final_path = wrap_fcs_as_zip(
                final_path, raw_file_name or final_name, zip_path
            )
            final_name = f"{file_id}.zip"

        try:
            sha256 = file_sha256(final_path)
        except OSError:
            sha256 = None
        file_instance = FileModel.objects.create(
            file=final_path,
            file_name=raw_file_name or final_name,
            sha256=sha256,
            experiment=experiment,
        )

        experiment.file_status = "uploaded"
        experiment.status = "processing"
        experiment.save(update_fields=["file_status", "status"])

        try:
            extract_metadata_from_zip(file_instance)
        except Exception as e:
            logger.error(
                "Erro ao extrair metadados do experimento %s: %s",
                experiment.id,
                e,
                exc_info=True,
            )
            experiment.status = "error"
            experiment.error_info = {
                "error_message": str(e),
                "details": traceback.format_exc(),
            }
            experiment.save(update_fields=["status", "error_info"])
            return Response(
                {"status": "error", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({"status": "done"})


def _annotate_list_meta(qs, user):
    """BE-21: anota `my_role` e `preview_available` sem N+1.

    `my_role`: "owner" em experimento pessoal do próprio usuário; em
    experimento de organização, o `role.name` da Membership ativa (o papel
    efetivo sobre o experimento é o papel na org — ADR-0003). Nulo quando
    nenhum dos dois se aplica (ex.: super admin vendo experimento alheio).
    """
    role_sq = Membership.objects.filter(
        user=user,
        organization_id=OuterRef("organization_id"),
        status="active",
    ).values("role__name")[:1]
    return qs.annotate(
        my_role=Case(
            When(organization__isnull=True, created_by=user, then=Value("owner")),
            default=Subquery(role_sq, output_field=CharField()),
            output_field=CharField(),
        ),
        preview_available=Exists(
            FileDataModel.objects.filter(experiment_id=OuterRef("pk"), active=True)
        ),
        # BE-22 v1: "está compensado" = alguma amostra ativa traz matriz
        # de spillover embutida nos headers FCS crus ($SPILLOVER/$COMP).
        compensated=Exists(
            FileDataModel.objects.filter(
                experiment_id=OuterRef("pk"),
                active=True,
                headers__has_any_keys=[
                    "$SPILLOVER",
                    "$spillover",
                    "$Spillover",
                    "$COMP",
                    "$comp",
                ],
            )
        ),
    )


class ExperimentListView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="include_inactive",
                type=bool,
                required=False,
                description="Inclui experimentos inativados na listagem (default: false)",
            )
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        request=ExperimentCreateSerializer,
        responses={201: ListExperimentSerializer},
    )
    def post(self, request, *args, **kwargs):
        """Cria experimento sem arquivo (BE-24).

        Nasce ``status="new"``/``file_status="pending"``; as amostras chegam
        depois via ``/experiment/<id>/files/init`` — a extração leva o
        experimento a ``done`` sozinha.
        """
        return super().post(request, *args, **kwargs)

    def get_serializer_class(self):
        if self.request.method == "POST":
            return ExperimentCreateSerializer
        return ListExperimentSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return _invalid(serializer)
        data = serializer.validated_data
        try:
            experiment = ExperimentModel.objects.create(
                title=data["title"],
                type=data["type"],
                description=data.get("description", ""),
                organization_id=data.get("organizationId"),
                created_by=request.user,
            )
        except IntegrityError:
            return Response(
                {"detail": "Título já criado para esse laboratório."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # BE-34: subsamples do setup inicial (ex.: grupo de controles).
        for spec in data.get("subsamples") or []:
            SubsampleModel.objects.create(
                experiment=experiment,
                name=spec["name"],
                control_type=spec.get("control_type"),
                control_channel=spec.get("control_channel", ""),
                created_by=request.user,
            )
        return Response(
            ListExperimentSerializer(experiment).data, status=status.HTTP_201_CREATED
        )

    def get_queryset(self):
        include_inactive = self.request.query_params.get("include_inactive") == "true"
        return _annotate_list_meta(
            experiments_visible_to(
                self.request.user, include_inactive=include_inactive
            ),
            self.request.user,
        )


class ExperimentTypeListCreateView(generics.ListCreateAPIView):
    """Vocabulário de tipos de experimento (BE-28, ADR-0023).

    GET lista os tipos ativos ordenados (autocomplete do front) para todo
    autenticado; POST cria um tipo novo e é **admin-only** — fora do
    contexto de um experimento, só admin introduz entradas soltas. A
    criação é idempotente e case-insensitive: um nome já existente devolve
    a entrada canônica com 200 em vez de erro.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ExperimentTypeSerializer

    def get_queryset(self):
        return ExperimentTypeModel.objects.filter(active=True).order_by(Lower("name"))

    def create(self, request, *args, **kwargs):
        if not can_create_experiment_type(request.user):
            return Response(
                {"detail": "Criar tipos de experimento exige perfil admin."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return _invalid(serializer)
        name = serializer.validated_data["name"]
        existing = ExperimentTypeModel.objects.filter(
            name_normalized=ExperimentTypeModel.normalize(name)
        ).first()
        if existing is not None:
            return Response(
                ExperimentTypeSerializer(existing).data, status=status.HTTP_200_OK
            )
        obj = ExperimentTypeModel.resolve(name, request.user)
        return Response(
            ExperimentTypeSerializer(obj).data, status=status.HTTP_201_CREATED
        )


class RetrieveDeleteExperimentView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "experiment_id"
    serializer_class = ListExperimentSerializer
    # PUT exigiria o payload completo do experimento; a edição é sempre parcial.
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return _annotate_list_meta(
            experiments_visible_to(self.request.user), self.request.user
        )

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return UpdateExperimentSerializer
        return ListExperimentSerializer

    def can_write(self, experiment) -> bool:
        user = self.request.user
        if user.is_super_admin or experiment.created_by_id == user.id:
            return True
        if experiment.organization_id is None:
            return False
        return user.memberships.filter(
            organization_id=experiment.organization_id, status="active"
        ).exists()

    def _resolve_move(self, request, experiment):
        """Valida o PATCH de contexto (`organization_id`) — mover, não copiar.

        Devolve (organization_id, None) autorizado ou (None, Response) de erro.
        """
        if not can_move_experiment(request.user, experiment):
            return None, Response(
                {"detail": "Mover exige ser o dono ou admin na origem do experimento."},
                status=status.HTTP_403_FORBIDDEN,
            )
        raw = request.data.get("organization_id")
        if raw in (None, ""):
            return None, None
        try:
            organization_id = int(raw)
        except (TypeError, ValueError):
            return None, Response(
                {"detail": "organization_id inválido."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not Organization.objects.filter(id=organization_id).exists():
            return None, Response(
                {"detail": "Laboratório não encontrado."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not is_org_member(request.user, organization_id):
            return None, Response(
                {"detail": "Você não é membro do laboratório de destino."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return organization_id, None

    def update(self, request, *args, **kwargs):
        experiment = self.get_object()
        moving = "organization_id" in request.data
        if moving:
            organization_id, error = self._resolve_move(request, experiment)
            if error:
                return error
            clash = ExperimentModel.objects.filter(
                title=experiment.title,
                created_by_id=experiment.created_by_id,
                organization_id=organization_id,
                active=True,
            ).exclude(id=experiment.id)
            if clash.exists():
                return Response(
                    {
                        "detail": "Já existe um experimento ativo com este "
                        "título no destino."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif not self.can_write(experiment):
            return Response(
                {"detail": "Você não tem permissão para editar este experimento."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(experiment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save()
            if moving:
                experiment.organization_id = organization_id
                experiment.save(update_fields=["organization"])
        except IntegrityError:
            return Response(
                {"detail": "Título já criado para esse laboratório."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Reanota sem re-escopar: mover para contexto pessoal pode tirar o
        # experimento do experiments_visible_to do próprio editor (admin de
        # org movendo para fora da org).
        experiment = _annotate_list_meta(
            ExperimentModel.objects.filter(pk=experiment.pk), request.user
        ).get()
        return Response(
            ListExperimentSerializer(experiment).data, status=status.HTTP_200_OK
        )

    def perform_destroy(self, instance):
        # ADR-0005: DELETE é arquivamento — o experimento sai das listagens
        # (experiments_visible_to filtra active) mas os dados permanecem.
        # Exige dono ou admin na origem: inativar muda o que os outros veem.
        if not can_move_experiment(self.request.user, instance):
            raise PermissionDenied(
                "Excluir exige ser o dono ou admin na origem do experimento."
            )
        if instance.active:
            instance.active = False
            instance.save(update_fields=["active"])
            record_revision(
                experiment=instance,
                action=AnalysisRevision.ACTION_DISABLE,
                target_type=AnalysisRevision.TARGET_EXPERIMENT,
                target_id=instance.id,
                user=self.request.user,
                payload_before={"targets": {str(instance.id): {"active": True}}},
                payload_after={"targets": {str(instance.id): {"active": False}}},
                affected_ids=[instance.id],
                summary=f'desativou o experimento "{instance.title}"',
            )


# Resolução do thumbnail do card de experimento (BE-21) — baixa o suficiente
# para uma resposta JSON pequena e cacheável.
PREVIEW_BINS = 48


class ExperimentPreviewView(APIView):
    """GET /experiment/<id>/preview — histograma 2D de baixa resolução.

    Miniatura do plot (FSC-A × SSC-A, ou os dois primeiros canais de
    `experiment.values`) da primeira amostra ativa. O front renderiza num
    <canvas> com a colorscale do tema; payload segue o formato de
    `compute_density`. 204 enquanto o experimento não tem dado pronto;
    404 quando não há amostra ativa.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={
            200: inline_serializer(
                name="ExperimentPreviewResponse",
                fields={
                    "file_data_id": serializers.IntegerField(),
                    "histogram": serializers.ListField(),
                    "x_edges": serializers.ListField(),
                    "y_edges": serializers.ListField(),
                    "x_label": serializers.CharField(),
                    "y_label": serializers.CharField(),
                },
            ),
            204: None,
            404: None,
        }
    )
    def get(self, request, experiment_id):
        experiment = get_object_or_404(
            experiments_visible_to(request.user), id=experiment_id
        )
        if experiment.status != "done":
            return Response(status=status.HTTP_204_NO_CONTENT)

        file_data = (
            FileDataModel.objects.filter(experiment=experiment, active=True)
            .order_by("id")
            .first()
        )
        if file_data is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        channels = [v for v in (experiment.values or []) if v]
        if len(channels) >= 2:
            x_param, y_param = channels[0], channels[1]
        else:
            x_param, y_param = "FSC-A", "SSC-A"

        x_scale = default_scale(x_param)
        y_scale = default_scale(y_param)
        # Preview reflete a análise real: com compensação aplicada, a
        # thumbnail mostra o dado compensado (BE-22) — e a matriz entra na
        # chave para não servir thumbnail crua como compensada.
        applied = applied_compensation(experiment)
        cache_key = density_cache_key(
            "preview",
            file_data.id,
            experiment.id,
            x_param,
            y_param,
            "heatmap",
            PREVIEW_BINS,
            0,
            x_scale,
            y_scale,
            DEFAULT_COFACTOR,
            0,
        )
        cache_key += f":comp{applied.id if applied else 0}"
        cached = get_cached_density(cache_key)
        if cached is not None:
            return Response(cached, status=status.HTTP_200_OK)

        dataset = normalize_columns(file_data.get_dataframe())
        if applied:
            dataset = apply_compensation(dataset, applied.channels, applied.matrix)
        result = compute_density(
            dataset,
            x_param,
            y_param,
            PREVIEW_BINS,
            x_scale,
            y_scale,
        )
        if result is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        payload = {
            "file_data_id": file_data.id,
            "x_label": x_param,
            "y_label": y_param,
            **result,
        }
        set_cached_density(cache_key, payload)
        return Response(payload, status=status.HTTP_200_OK)


class ExperimentEmbeddedCompensationView(APIView):
    """GET /experiment/<id>/compensations/embedded — matriz de spillover
    embutida nos headers FCS (BE-22 v1).

    Devolve ``{channels, matrix, file_data_id, source}`` da primeira
    amostra ativa com ``$SPILLOVER``/``$COMP`` parseável; 204 quando
    nenhuma amostra traz a keyword. Persistir como CompensationMatrix e
    aplicar na leitura são as próximas etapas do PRD.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={
            200: inline_serializer(
                name="EmbeddedCompensationResponse",
                fields={
                    "channels": serializers.ListField(),
                    "matrix": serializers.ListField(),
                    "file_data_id": serializers.IntegerField(),
                    "source": serializers.CharField(),
                },
            ),
            204: None,
        }
    )
    def get(self, request, experiment_id):
        from fcs_parser.services.compensation import (
            experiment_embedded_compensation,
        )

        experiment = get_object_or_404(
            experiments_visible_to(request.user), id=experiment_id
        )
        embedded = experiment_embedded_compensation(experiment)
        if embedded is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(embedded, status=status.HTTP_200_OK)


class ExperimentCompensationListView(generics.ListAPIView):
    """GET/POST /experiment/<id>/compensations/ — matrizes do experimento.

    GET lista as ativas (BE-22). POST cria uma matriz ``source="manual"``
    do zero ou derivada de outra (BE-35) — os valores são imutáveis, o
    ajuste de uma existente é sempre uma nova com ``derived_from``.
    """

    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        from analytics.serializers import CompensationMatrixSerializer

        return CompensationMatrixSerializer

    def get_queryset(self):
        from analytics.models import CompensationMatrix

        experiment = get_object_or_404(
            experiments_visible_to(self.request.user),
            id=self.kwargs["experiment_id"],
        )
        return CompensationMatrix.objects.filter(
            experiment=experiment, active=True
        ).order_by("-created_at")

    def post(self, request, experiment_id):
        from analytics.models import CompensationMatrix
        from analytics.serializers import (
            CompensationManualCreateSerializer,
            CompensationMatrixSerializer,
        )
        from fcs_parser.services.compensation import set_applied_compensation

        experiment = get_object_or_404(
            experiments_visible_to(request.user), id=experiment_id
        )
        require_can_edit_experiment(request.user, experiment)

        payload = CompensationManualCreateSerializer(
            data=request.data, context={"experiment": experiment}
        )
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        origin = data.get("derived_from")
        if data["name"]:
            name = data["name"]
        elif origin is not None:
            name = f"{origin.name or f'matriz {origin.id}'} (ajustada)"
        else:
            name = "Manual"

        matrix = CompensationMatrix.objects.create(
            experiment=experiment,
            name=name,
            channels=data["channels"],
            matrix=data["matrix"],
            source=CompensationMatrix.SOURCE_MANUAL,
            derived_from=origin,
            created_by=request.user,
        )
        # apply=true: cria e já aplica — grava a revisão
        # `compensation_apply` e invalida densidade de graça.
        if data["apply"]:
            set_applied_compensation(experiment, matrix, request.user)
            matrix.refresh_from_db()
        return Response(
            CompensationMatrixSerializer(matrix).data,
            status=status.HTTP_201_CREATED,
        )


class ExperimentCompensationFromHeaderView(APIView):
    """POST .../compensations/from-header — materializa a matriz embutida."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={201: None, 409: None})
    def post(self, request, experiment_id):
        from analytics.models import CompensationMatrix
        from analytics.serializers import CompensationMatrixSerializer
        from fcs_parser.services.compensation import (
            experiment_embedded_compensation,
        )

        experiment = get_object_or_404(
            experiments_visible_to(request.user), id=experiment_id
        )
        require_can_edit_experiment(request.user, experiment)

        embedded = experiment_embedded_compensation(experiment)
        if embedded is None:
            return Response(
                {"detail": "Nenhuma amostra ativa traz $SPILLOVER/$COMP."},
                status=status.HTTP_409_CONFLICT,
            )
        matrix = CompensationMatrix.objects.create(
            experiment=experiment,
            name=request.data.get("name", "") or "Matriz do arquivo",
            channels=embedded["channels"],
            matrix=embedded["matrix"],
            source=CompensationMatrix.SOURCE_FCS_HEADER,
            created_by=request.user,
        )
        # apply=true: materializa e já aplica — "usar a compensação que
        # veio da aquisição" em um passo só.
        if request.data.get("apply"):
            from fcs_parser.services.compensation import (
                set_applied_compensation,
            )

            set_applied_compensation(experiment, matrix, request.user)
            matrix.refresh_from_db()
        return Response(
            CompensationMatrixSerializer(matrix).data,
            status=status.HTTP_201_CREATED,
        )


class ExperimentCompensationComputeView(APIView):
    """POST .../compensations/compute — calcula a matriz dos controles.

    Sem payload, deriva canal→controle dos subsamples marcados
    (ADR-0019). ``negative``/``controls`` no payload são overrides de
    file_data_ids. Erros de domínio viram 400 com a mensagem explicável.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, experiment_id):
        from analytics.models import CompensationMatrix
        from analytics.serializers import (
            CompensationComputeSerializer,
            CompensationMatrixSerializer,
        )
        from fcs_parser.services.compensation import (
            compute_spillover_matrix,
            experiment_controls,
        )

        experiment = get_object_or_404(
            experiments_visible_to(request.user), id=experiment_id
        )
        require_can_edit_experiment(request.user, experiment)

        payload = CompensationComputeSerializer(
            data=request.data, context={"experiment": experiment}
        )
        payload.is_valid(raise_exception=True)

        unstained, stains = experiment_controls(experiment)
        if "negative" in payload.validated_data:
            unstained = payload.validated_data["negative"]
        if "controls" in payload.validated_data:
            stains = {
                channel: ids
                for channel, ids in payload.validated_data["controls"].items()
            }

        # Overrides apontam para amostras do próprio experimento.
        referenced = list(unstained) + [i for ids in stains.values() for i in ids]
        valid_ids = set(
            FileDataModel.objects.filter(
                experiment=experiment, id__in=referenced, active=True
            ).values_list("id", flat=True)
        )
        invalid = sorted(set(referenced) - valid_ids)
        if invalid:
            return Response(
                {"detail": f"Amostras de controle inválidas: {invalid}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            computed = compute_spillover_matrix(unstained, stains)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        matrix = CompensationMatrix.objects.create(
            experiment=experiment,
            name=payload.validated_data.get("name") or "Calculada de controles",
            channels=computed["channels"],
            matrix=computed["matrix"],
            source=CompensationMatrix.SOURCE_COMPUTED,
            created_by=request.user,
        )
        return Response(
            CompensationMatrixSerializer(matrix).data,
            status=status.HTTP_201_CREATED,
        )


class ExperimentCompensationApplyView(APIView):
    """POST .../compensations/<matrix_id>/apply — torna a matriz a ativa."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: None, 404: None})
    def post(self, request, experiment_id, matrix_id):
        from analytics.models import CompensationMatrix
        from fcs_parser.services.compensation import set_applied_compensation

        experiment = get_object_or_404(
            experiments_visible_to(request.user), id=experiment_id
        )
        require_can_edit_experiment(request.user, experiment)
        matrix = get_object_or_404(
            CompensationMatrix, id=matrix_id, experiment=experiment, active=True
        )
        set_applied_compensation(experiment, matrix, request.user)
        return Response({"is_applied": True}, status=status.HTTP_200_OK)


class ExperimentCompensationRemoveView(APIView):
    """POST .../compensations/remove — desliga a compensação aplicada."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: None})
    def post(self, request, experiment_id):
        from fcs_parser.services.compensation import set_applied_compensation

        experiment = get_object_or_404(
            experiments_visible_to(request.user), id=experiment_id
        )
        require_can_edit_experiment(request.user, experiment)
        set_applied_compensation(experiment, None, request.user)
        return Response({"is_applied": False}, status=status.HTTP_200_OK)


class ExperimentCompensationPreviewView(APIView):
    """POST .../compensations/preview — densidade de uma matriz ad-hoc (BE-36).

    Read-only de fato: nunca cria CompensationMatrix nem AnalysisRevision
    — é o caminho do FE-41 para mostrar o efeito da matriz sendo editada
    sem poluir a lista de matrizes nem o histórico do experimento.
    Leitura basta (``experiments_visible_to``): nada é escrito.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=CompensationPreviewSerializer,
        responses={200: None, 400: None},
    )
    def post(self, request, experiment_id):
        import hashlib

        experiment = get_object_or_404(
            experiments_visible_to(request.user), id=experiment_id
        )
        payload = CompensationPreviewSerializer(
            data=request.data, context={"experiment": experiment}
        )
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        file_data = data["file"]
        params = data.get("params") or {}

        x_param = data["x_axis"]
        y_param = data["y_axis"]
        try:
            mode = str(params.get("mode", "heatmap"))
            bins = int(params.get("bins", 200))
            sample = int(params.get("sample", 5000))
            x_scale = params.get("xscale") or default_scale(x_param)
            y_scale = params.get("yscale") or default_scale(y_param)
            cofactor = float(params.get("cofactor", DEFAULT_COFACTOR))
            cutoff = max(int(params.get("cutoff", 0)), 0)
            x_range = parse_range(params, "xmin", "xmax")
            y_range = parse_range(params, "ymin", "ymax")
        except (TypeError, ValueError):
            return Response(
                {"params": "Parâmetros de renderização inválidos."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cache por conteúdo (ADR-0004): o sha256 do payload normalizado
        # entra no lugar do :comp<id> — matrizes em edição mudam a cada
        # tecla, mas valores repetidos reutilizam o cache do density.
        digest = hashlib.sha256(
            json.dumps(
                {
                    "channels": data["channels"],
                    "matrix": data["matrix"],
                    "file": file_data.id,
                    "x_axis": x_param,
                    "y_axis": y_param,
                    "params": params,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:16]
        cache_key = density_cache_key(
            "file",
            file_data.id,
            file_data.id,
            x_param,
            y_param,
            mode,
            bins,
            sample,
            x_scale,
            y_scale,
            cofactor,
            cutoff,
        )
        if x_range:
            cache_key += f":xr{x_range[0]}:{x_range[1]}"
        if y_range:
            cache_key += f":yr{y_range[0]}:{y_range[1]}"
        cache_key += f":comp-preview:{digest}"
        cached = get_cached_density(cache_key)
        if cached is not None:
            return Response(cached, status=status.HTTP_200_OK)

        dataset = normalize_columns(file_data.get_dataframe())
        dataset = apply_compensation(dataset, data["channels"], data["matrix"])

        base = {
            "mode": mode,
            "total_events": len(dataset),
            "x_label": x_param,
            "y_label": y_param,
        }
        if mode == "scatter":
            result = subsample_scatter(
                dataset,
                x_param,
                y_param,
                sample,
                x_scale,
                y_scale,
                cofactor,
                x_range,
                y_range,
            )
        elif mode == "histogram":
            result = compute_histogram(
                dataset, x_param, bins, x_scale, cofactor, x_range
            )
        else:
            result = compute_density(
                dataset,
                x_param,
                y_param,
                bins,
                x_scale,
                y_scale,
                cofactor,
                cutoff,
                x_range,
                y_range,
            )

        if result is None:
            return Response(
                {"detail": f"Columns '{x_param}' or '{y_param}' not found in dataset."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        response_payload = {**base, **result}
        set_cached_density(cache_key, response_payload)
        return Response(response_payload, status=status.HTTP_200_OK)


class ExperimentRestoreView(APIView):
    """POST /experiment/<experiment_id>/restore — reativa um experimento.

    O DELETE é arquivamento (active=False); este endpoint é o caminho de
    volta. Exige a mesma permissão da inativação: dono ou admin na origem.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses=ListExperimentSerializer)
    def post(self, request, experiment_id):
        experiment = get_object_or_404(
            experiments_visible_to(request.user, include_inactive=True),
            id=experiment_id,
        )
        if not can_move_experiment(request.user, experiment):
            raise PermissionDenied(
                "Reativar exige ser o dono ou admin na origem do experimento."
            )
        if not experiment.active:
            # Título é único só entre ativos (ADR-0015): reativar pode colidir
            # com um ativo que reocupou o título — conflito, não 500.
            clash = ExperimentModel.objects.filter(
                title=experiment.title,
                created_by_id=experiment.created_by_id,
                organization_id=experiment.organization_id,
                active=True,
            ).exists()
            if clash:
                return Response(
                    {
                        "detail": "Já existe um experimento ativo com este título "
                        "neste contexto."
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            experiment.active = True
            try:
                experiment.save(update_fields=["active"])
            except IntegrityError:
                return Response(
                    {
                        "detail": "Já existe um experimento ativo com este título "
                        "neste contexto."
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            record_revision(
                experiment=experiment,
                action=AnalysisRevision.ACTION_ENABLE,
                target_type=AnalysisRevision.TARGET_EXPERIMENT,
                target_id=experiment.id,
                user=request.user,
                payload_before={"targets": {str(experiment.id): {"active": False}}},
                payload_after={"targets": {str(experiment.id): {"active": True}}},
                affected_ids=[experiment.id],
                summary=f'reativou o experimento "{experiment.title}"',
            )
        return Response(
            ListExperimentSerializer(experiment).data, status=status.HTTP_200_OK
        )


class DisableFileDataView(APIView):
    """POST /experiment/file/<file_id>/disable — desabilita (freezer) uma amostra.

    Nada é apagado: dados em disco e gates continuam intactos e a amostra pode
    ser reativada em /enable.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=None,
        responses=inline_serializer(
            name="FileDataActiveResponse",
            fields={
                "id": serializers.IntegerField(),
                "file_name": serializers.CharField(),
                "active": serializers.BooleanField(),
                "deactivated_at": serializers.DateTimeField(allow_null=True),
            },
        ),
    )
    def post(self, request, file_id):
        file_data = get_object_or_404(
            file_data_visible_to(request.user).select_related("experiment"),
            id=file_id,
        )
        require_can_edit_file_data(request.user, file_data)

        if file_data.active:
            file_data.active = False
            file_data.deactivated_at = timezone.now()
            file_data.deactivated_by = request.user
            file_data.save(update_fields=["active", "deactivated_at", "deactivated_by"])
            invalidate_density(file_data.id)
            record_revision(
                experiment=file_data.experiment,
                action=AnalysisRevision.ACTION_DISABLE,
                target_type=AnalysisRevision.TARGET_FILE,
                target_id=file_data.id,
                user=request.user,
                payload_before={"targets": {str(file_data.id): {"active": True}}},
                payload_after={"targets": {str(file_data.id): {"active": False}}},
                affected_ids=[file_data.id],
                summary=f'desabilitou a amostra "{file_data.file_name}"',
            )

        return Response(
            {
                "id": file_data.id,
                "file_name": file_data.file_name,
                "active": file_data.active,
                "deactivated_at": file_data.deactivated_at,
            },
            status=status.HTTP_200_OK,
        )


class EnableFileDataView(APIView):
    """POST /experiment/file/<file_id>/enable — reativa uma amostra desabilitada."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses=None)
    def post(self, request, file_id):
        file_data = get_object_or_404(
            file_data_visible_to(request.user).select_related("experiment"),
            id=file_id,
        )
        require_can_edit_file_data(request.user, file_data)

        if not file_data.active:
            file_data.active = True
            file_data.deactivated_at = None
            file_data.deactivated_by = None
            file_data.save(update_fields=["active", "deactivated_at", "deactivated_by"])
            record_revision(
                experiment=file_data.experiment,
                action=AnalysisRevision.ACTION_ENABLE,
                target_type=AnalysisRevision.TARGET_FILE,
                target_id=file_data.id,
                user=request.user,
                payload_before={"targets": {str(file_data.id): {"active": False}}},
                payload_after={"targets": {str(file_data.id): {"active": True}}},
                affected_ids=[file_data.id],
                summary=f'reativou a amostra "{file_data.file_name}"',
            )

        return Response(
            {
                "id": file_data.id,
                "file_name": file_data.file_name,
                "active": file_data.active,
                "deactivated_at": file_data.deactivated_at,
            },
            status=status.HTTP_200_OK,
        )


class SubsampleListCreateView(generics.ListCreateAPIView):
    """GET/POST /experiment/<experiment_id>/subsamples/

    A engine cria os subsamples a partir dos diretórios do ZIP; este endpoint
    existe para o cliente listar e criar agrupamentos próprios na UI.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = SubsampleSerializer

    def get_experiment(self):
        return get_object_or_404(
            experiments_visible_to(self.request.user),
            id=self.kwargs["experiment_id"],
        )

    def get_serializer_context(self):
        # O serializer valida control_channel contra os canais do experimento.
        ctx = super().get_serializer_context()
        ctx["experiment"] = self.get_experiment()
        return ctx

    def get_queryset(self):
        queryset = SubsampleModel.objects.filter(
            experiment__in=experiments_visible_to(self.request.user),
            experiment_id=self.kwargs["experiment_id"],
        ).prefetch_related("files")
        if self.request.query_params.get("include_inactive") != "true":
            queryset = queryset.filter(active=True)
        return queryset.order_by("name")

    def create(self, request, *args, **kwargs):
        experiment = self.get_experiment()
        require_can_edit_experiment(request.user, experiment)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            subsample = serializer.save(experiment=experiment, created_by=request.user)
        except IntegrityError:
            return Response(
                {"name": "Já existe um subsample com esse nome neste experimento."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record_revision(
            experiment=experiment,
            action=AnalysisRevision.ACTION_CREATE,
            target_type=AnalysisRevision.TARGET_SUBSAMPLE,
            target_id=subsample.id,
            user=request.user,
            payload_after={
                "targets": {str(subsample.id): {"name": subsample.name, "active": True}}
            },
            affected_ids=[subsample.id],
            summary=f'criou o subsample "{subsample.name}"',
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SubsampleDetailView(generics.RetrieveUpdateDestroyAPIView):
    """PATCH renomeia o subsample; DELETE apenas o inativa (nada é deletado).

    Ao inativar, as amostras do subsample voltam para "sem subsample" — os
    dados e os gates seguem intactos.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = SubsampleSerializer

    def get_queryset(self):
        return SubsampleModel.objects.filter(
            experiment__in=experiments_visible_to(self.request.user),
            experiment_id=self.kwargs["experiment_id"],
        ).select_related("experiment")

    def check_can_edit(self, subsample):
        require_can_edit_experiment(self.request.user, subsample.experiment)

    def perform_update(self, serializer):
        self.check_can_edit(serializer.instance)
        old_name = serializer.instance.name
        old_tags = sorted(serializer.instance.tags.values_list("id", flat=True))
        try:
            subsample = serializer.save()
        except IntegrityError:
            raise serializers.ValidationError(
                {"name": "Já existe um subsample com esse nome neste experimento."}
            )
        new_tags = sorted(subsample.tags.values_list("id", flat=True))
        if new_tags != old_tags:
            names = ", ".join(tag.name for tag in subsample.tags.all()) or "—"
            record_revision(
                experiment=subsample.experiment,
                action=AnalysisRevision.ACTION_TAGS,
                target_type=AnalysisRevision.TARGET_SUBSAMPLE,
                target_id=subsample.id,
                user=self.request.user,
                payload_before={"targets": {str(subsample.id): {"tags": old_tags}}},
                payload_after={"targets": {str(subsample.id): {"tags": new_tags}}},
                affected_ids=[subsample.id],
                summary=f'etiquetou o subsample "{subsample.name}": {names}',
            )
        if subsample.name != old_name:
            record_revision(
                experiment=subsample.experiment,
                action=AnalysisRevision.ACTION_RENAME,
                target_type=AnalysisRevision.TARGET_SUBSAMPLE,
                target_id=subsample.id,
                user=self.request.user,
                payload_before={"targets": {str(subsample.id): {"name": old_name}}},
                payload_after={
                    "targets": {str(subsample.id): {"name": subsample.name}}
                },
                affected_ids=[subsample.id],
                summary=f'renomeou o subsample "{old_name}" → "{subsample.name}"',
            )

    def perform_destroy(self, instance):
        self.check_can_edit(instance)
        linked_ids = list(instance.files.values_list("id", flat=True))
        if instance.active:
            instance.active = False
            instance.save(update_fields=["active"])
        instance.files.update(subsample=None)
        record_revision(
            experiment=instance.experiment,
            action=AnalysisRevision.ACTION_DELETE,
            target_type=AnalysisRevision.TARGET_SUBSAMPLE,
            target_id=instance.id,
            user=self.request.user,
            payload_before={
                "targets": {str(instance.id): {"active": True}},
                "unlinked_file_ids": linked_ids,
            },
            payload_after={"targets": {str(instance.id): {"active": False}}},
            affected_ids=[instance.id, *linked_ids],
            summary=(
                f'arquivou o subsample "{instance.name}"'
                + (
                    f" ({len(linked_ids)} amostra(s) desagrupada(s))"
                    if linked_ids
                    else ""
                )
            ),
        )


class FileSubsampleView(APIView):
    """PATCH /experiment/file/<file_id>/subsample — move a amostra de subsample.

    A engine sugere o vínculo pelo diretório do ZIP, mas quem decide é o
    cliente: `{"subsample": <id>}` move e `{"subsample": null}` desagrupa.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=inline_serializer(
            name="FileSubsampleRequest",
            fields={
                "subsample": serializers.IntegerField(allow_null=True),
            },
        ),
        responses=ListFileDataSerializer,
    )
    def patch(self, request, file_id):
        file_data = get_object_or_404(
            file_data_visible_to(request.user).select_related("experiment"),
            id=file_id,
        )
        require_can_edit_file_data(request.user, file_data)

        if "subsample" not in request.data:
            return Response(
                {"subsample": "Campo obrigatório (use null para desagrupar)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subsample_id = request.data.get("subsample")
        subsample = None
        if subsample_id is not None:
            subsample = SubsampleModel.objects.filter(
                id=subsample_id,
                experiment_id=file_data.experiment_id,
                active=True,
            ).first()
            if subsample is None:
                return Response(
                    {"subsample": "Subsample inválido para este experimento."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        old_subsample_id = file_data.subsample_id
        file_data.subsample = subsample
        file_data.save(update_fields=["subsample"])
        if old_subsample_id != file_data.subsample_id:
            record_revision(
                experiment=file_data.experiment,
                action=AnalysisRevision.ACTION_MOVE_SUBSAMPLE,
                target_type=AnalysisRevision.TARGET_FILE,
                target_id=file_data.id,
                user=request.user,
                payload_before={
                    "targets": {str(file_data.id): {"subsample_id": old_subsample_id}}
                },
                payload_after={
                    "targets": {
                        str(file_data.id): {"subsample_id": file_data.subsample_id}
                    }
                },
                affected_ids=[file_data.id],
                summary=(
                    f'moveu a amostra "{file_data.file_name}" para '
                    f'"{subsample.name}"'
                    if subsample
                    else f'removeu a amostra "{file_data.file_name}" do subsample'
                ),
            )

        return Response(
            ListFileDataSerializer(file_data).data, status=status.HTTP_200_OK
        )


class FilePlotConfigView(APIView):
    """PATCH /experiment/file/<file_id>/plot-config — config de visualização
    da amostra raiz.

    Mesmo papel do ``plot_config`` de gate (PATCH /analytics/gate/<id>),
    para quando a fonte do plot é o arquivo inteiro. É preferência de
    exibição, não análise — não gera revisão nem invalida densidade.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(request=FilePlotConfigSerializer, responses=ListFileDataSerializer)
    def patch(self, request, file_id):
        file_data = get_object_or_404(
            file_data_visible_to(request.user).select_related("experiment"),
            id=file_id,
        )
        require_can_edit_file_data(request.user, file_data)

        payload = FilePlotConfigSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        file_data.plot_config = payload.validated_data["plot_config"]
        file_data.save(update_fields=["plot_config"])
        return Response(
            ListFileDataSerializer(file_data).data, status=status.HTTP_200_OK
        )


class GetExperimentFiles(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    lookup_url_kwarg = "experiment_id"
    serializer_class = ListFileDataSerializer

    def get_queryset(self):
        experiment_id = self.kwargs.get("experiment_id")
        queryset = file_data_visible_to(self.request.user).filter(
            experiment_id=experiment_id
        )
        if self.request.query_params.get("include_inactive") != "true":
            queryset = queryset.filter(active=True)
        # BE-34: tags próprias + herdadas do subsample — prefetch/select
        # evitam N+1 ao calcular `inherited_tags` por amostra.
        return queryset.select_related("subsample").prefetch_related(
            "tags", "subsample__tags"
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="include_inactive",
                type=bool,
                required=False,
                description="Inclui amostras desabilitadas na listagem (default: false)",
            )
        ]
    )
    def list(self, request, *args, **kwargs):
        from analytics.services.branches import resolve_branch
        from fcs_parser.permissions import experiments_visible_to

        # Retrieve the queryset of files
        queryset = self.get_queryset()

        # BE-23: a árvore é da linha de análise pedida (?branch=<id>,
        # default main do experimento). Experimento invisível já resulta
        # em queryset vazio — mantemos [] em vez de 404 (contrato antigo).
        branch_id = request.query_params.get("branch")
        experiment = (
            experiments_visible_to(request.user)
            .filter(id=self.kwargs.get("experiment_id"))
            .first()
        )
        branch = resolve_branch(experiment, branch_id) if experiment else None

        # Manually create the response data with the gate tree
        data = []
        for file in queryset:
            # Get the flat serialized data for the file
            file_serializer = self.get_serializer(file)
            file_data = file_serializer.data

            # Build the gate tree for the current file
            gate_tree = GateModel.build_tree(file_data_id=file.id, branch=branch)

            # Add the built tree to the file data
            file_data["gates"] = gate_tree
            data.append(file_data)

        return Response(data)


class ListFileParams(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    lookup_field = "file_id"
    serializer_class = ParamListDataSerializer

    def get_queryset(self):
        file_id = self.kwargs.get("file_id")
        queryset = get_object_or_404(
            file_data_visible_to(self.request.user), id=file_id
        )
        return queryset

    def list(self, request, *args, **kwargs):
        limit = request.query_params.get("limit", 10000)
        try:
            limit = int(limit)
        except ValueError:
            limit = 10000

        file_data = self.get_queryset()
        if not file_data.active:
            return Response(
                {"detail": INACTIVE_FILE_DETAIL}, status=status.HTTP_409_CONFLICT
            )
        dataset = normalize_columns(file_data.get_dataframe())
        # BE-22: a tabela de eventos mostra o mesmo espaço que o usuário
        # vê/gateia — com compensação ativa, os valores já vêm corrigidos.
        applied = applied_compensation(file_data.experiment)
        if applied:
            dataset = apply_compensation(dataset, applied.channels, applied.matrix)
        dataset = dataset.head(limit)
        file_data.data_set = json.loads(dataset.to_json(orient="records"))
        serializer = self.serializer_class(file_data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class FileDensityView(APIView):
    permission_classes = [IsAuthenticated]
    """Return density (heatmap) or subsampled scatter for a file's data."""

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="x",
                type=str,
                required=True,
                description="X-axis parameter (e.g. FSC-A)",
            ),
            OpenApiParameter(
                name="y",
                type=str,
                required=True,
                description="Y-axis parameter (e.g. SSC-A)",
            ),
            OpenApiParameter(
                name="mode",
                type=str,
                required=False,
                description="'heatmap' (default) or 'scatter'",
            ),
            OpenApiParameter(
                name="bins",
                type=int,
                required=False,
                description="Bins for heatmap (default 200)",
            ),
            OpenApiParameter(
                name="sample",
                type=int,
                required=False,
                description="Max points for scatter (default 5000)",
            ),
            OpenApiParameter(
                name="xscale",
                type=str,
                required=False,
                description="'linear' or 'biex' (default: heuristic by channel)",
            ),
            OpenApiParameter(
                name="yscale",
                type=str,
                required=False,
                description="'linear' or 'biex' (default: heuristic by channel)",
            ),
            OpenApiParameter(
                name="cofactor",
                type=float,
                required=False,
                description="arcsinh cofactor for biex (default 150)",
            ),
            OpenApiParameter(
                name="cutoff",
                type=int,
                required=False,
                description="Heatmap density cutoff: bins with count <= cutoff become null/transparent (default 0)",
            ),
            OpenApiParameter(
                name="xmin",
                type=float,
                required=False,
                description="Lower bound for X axis (raw value)",
            ),
            OpenApiParameter(
                name="xmax",
                type=float,
                required=False,
                description="Upper bound for X axis (raw value)",
            ),
            OpenApiParameter(
                name="ymin",
                type=float,
                required=False,
                description="Lower bound for Y axis (raw value)",
            ),
            OpenApiParameter(
                name="ymax",
                type=float,
                required=False,
                description="Upper bound for Y axis (raw value)",
            ),
        ],
        responses=inline_serializer(
            name="FileDensityResponse",
            fields={
                "mode": serializers.CharField(),
                "total_events": serializers.IntegerField(),
                "x_label": serializers.CharField(),
                "y_label": serializers.CharField(),
            },
        ),
    )
    def get(self, request, file_id):
        x_param = request.query_params.get("x", "FSC-A")
        y_param = request.query_params.get("y", "SSC-A")
        mode = request.query_params.get("mode", "heatmap")
        bins = int(request.query_params.get("bins", 200))
        sample = int(request.query_params.get("sample", 5000))
        x_scale = request.query_params.get("xscale") or default_scale(x_param)
        y_scale = request.query_params.get("yscale") or default_scale(y_param)
        cofactor = float(request.query_params.get("cofactor", DEFAULT_COFACTOR))
        try:
            cutoff = max(int(request.query_params.get("cutoff", 0)), 0)
        except (TypeError, ValueError):
            cutoff = 0
        x_range = parse_range(request.query_params, "xmin", "xmax")
        y_range = parse_range(request.query_params, "ymin", "ymax")

        cache_key = density_cache_key(
            "file",
            file_id,
            file_id,
            x_param,
            y_param,
            mode,
            bins,
            sample,
            x_scale,
            y_scale,
            cofactor,
            cutoff,
        )
        if x_range:
            cache_key += f":xr{x_range[0]}:{x_range[1]}"
        if y_range:
            cache_key += f":yr{y_range[0]}:{y_range[1]}"
        file_data, error = get_active_file_data_or_error(request.user, file_id)
        if error:
            return error

        # BE-22: a matriz aplicada entra na chave de cache — densidade
        # compensada nunca é servida como se fosse crua.
        applied = applied_compensation(file_data.experiment)
        cache_key += f":comp{applied.id if applied else 0}"
        cached = get_cached_density(cache_key)
        if cached is not None:
            return Response(cached, status=status.HTTP_200_OK)

        dataset = normalize_columns(file_data.get_dataframe())
        if applied:
            dataset = apply_compensation(dataset, applied.channels, applied.matrix)

        base = {
            "mode": mode,
            "total_events": len(dataset),
            "x_label": x_param,
            "y_label": y_param,
        }

        if mode == "scatter":
            result = subsample_scatter(
                dataset,
                x_param,
                y_param,
                sample,
                x_scale,
                y_scale,
                cofactor,
                x_range,
                y_range,
            )
        elif mode == "histogram":
            result = compute_histogram(
                dataset, x_param, bins, x_scale, cofactor, x_range
            )
        else:
            result = compute_density(
                dataset,
                x_param,
                y_param,
                bins,
                x_scale,
                y_scale,
                cofactor,
                cutoff,
                x_range,
                y_range,
            )

        if result is None:
            return Response(
                {"detail": f"Columns '{x_param}' or '{y_param}' not found in dataset."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = {**base, **result}
        set_cached_density(cache_key, payload)
        return Response(payload, status=status.HTTP_200_OK)


class ProcessFileDataView(generics.CreateAPIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=None,
        responses=inline_serializer(
            name="ProcessFileDataResponse",
            fields={"message": serializers.CharField()},
        ),
    )
    def post(self, request, *args, **kwargs):
        from fcs_parser.services.process_experiment_file import process_experiment_zip

        file_id = kwargs.get("file_id")
        file = get_object_or_404(
            uploads_visible_to(request.user).select_related("experiment"),
            id=file_id,
        )
        experiment = file.experiment
        require_can_edit_experiment(request.user, experiment)
        if experiment.status == "processing":
            return Response(
                {"message": "The file is still being processed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        experiment.status = "processing"
        experiment.save(update_fields=["status"])

        try:
            process_experiment_zip(file)
        except Exception as e:
            logger.error(
                "Erro ao processar experimento %s: %s",
                experiment.id,
                e,
                exc_info=True,
            )
            experiment.status = "error"
            experiment.error_info = {
                "error_message": str(e),
                "details": traceback.format_exc(),
            }
            experiment.save(update_fields=["status", "error_info"])
            return Response(
                {"message": f"Processing failed: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {"message": "File processing completed."},
            status=status.HTTP_200_OK,
        )


class FileStatsView(APIView):
    permission_classes = [IsAuthenticated]
    """Return summary and per-channel statistics for the entire file (no gate).

    Computes the same metrics as gate analysis but on the full dataset so the
    frontend stats panel can show file-level statistics.
    """

    @extend_schema(
        responses=inline_serializer(
            name="FileStatsResponse",
            fields={
                "summary_metrics": serializers.DictField(),
                "channel_statistics": serializers.DictField(),
            },
        ),
    )
    def get(self, request, file_id):
        file_data, error = get_active_file_data_or_error(request.user, file_id)
        if error:
            return error
        dataset = normalize_columns(file_data.get_dataframe())
        applied = applied_compensation(file_data.experiment)
        if applied:
            dataset = apply_compensation(dataset, applied.channels, applied.matrix)

        if dataset.empty:
            return Response(
                {"detail": "Dataset vazio para este arquivo."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        total_events = len(dataset)
        all_channel_names = list(dataset.columns)

        channel_statistics = {}
        for channel in all_channel_names:
            channel_data = dataset[channel]
            if channel_data.empty:
                continue
            mean_val = float(channel_data.mean())
            median_val = float(channel_data.median())
            std_dev_val = float(channel_data.std())
            channel_statistics[channel] = {
                "mean_mfi": mean_val,
                "median_mfi": median_val,
                "std_dev": std_dev_val,
                "cv": (std_dev_val / mean_val * 100) if mean_val != 0 else 0,
            }

        payload = {
            "summary_metrics": {
                "count": total_events,
                "percent_of_total_population": 1.0,
                "percent_of_parent_population": 1.0,
            },
            "channel_statistics": channel_statistics,
        }

        return Response(payload, status=status.HTTP_200_OK)


class RecomputeFileDataView(APIView):
    permission_classes = [IsAuthenticated]
    """Reprocess a FileData from the experiment's ZIP (or legacy .fcs).

    Rebuilds the Parquet cache synchronously and invalidates density cache.
    """

    @extend_schema(
        request=None,
        responses=inline_serializer(
            name="RecomputeResponse",
            fields={
                "status": serializers.CharField(),
                "file_data_id": serializers.IntegerField(),
            },
        ),
    )
    def post(self, request, file_id):
        file_data, error = get_active_file_data_or_error(request.user, file_id)
        if error:
            return error
        require_can_edit_file_data(request.user, file_data)
        has_zip = bool(getattr(getattr(file_data.file, "file", None), "name", None))
        has_fcs = bool(file_data.fcs_path)
        if not has_zip and not has_fcs:
            return Response(
                {"detail": "Sem fonte (ZIP ou .fcs) para reprocessar este arquivo."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Force rebuild by clearing parquet_path; next get_dataframe() rebuilds.
        if file_data.parquet_path:
            try:
                if os.path.exists(file_data.parquet_path):
                    os.remove(file_data.parquet_path)
            except OSError:
                pass
            FileDataModel.objects.filter(pk=file_data.pk).update(parquet_path=None)

        # Trigger rebuild now.
        file_data.refresh_from_db()
        file_data.get_dataframe()
        invalidate_density(file_data.id)

        return Response(
            {"status": "completed", "file_data_id": file_data.id},
            status=status.HTTP_200_OK,
        )


class FileHeadersView(APIView):
    """GET /experiment/file/<file_id>/headers — metadados do header FCS.

    Devolve o dict `headers` persistido no parse (keywords do FCS 3.x
    normalizadas: `$date`, `$cyt`, `$btim`, `$etim`, `$op`, `tot`...).
    Amostras inativas continuam legíveis — os dados não são apagados.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={
            200: inline_serializer(
                name="FileHeadersResponse",
                fields={
                    "file_data_id": serializers.IntegerField(),
                    "file_name": serializers.CharField(),
                    "headers": serializers.DictField(),
                },
            ),
        },
    )
    def get(self, request, file_id):
        file_data = get_object_or_404(file_data_visible_to(request.user), id=file_id)
        return Response(
            {
                "file_data_id": file_data.id,
                "file_name": file_data.file_name,
                "headers": file_data.headers or {},
            },
            status=status.HTTP_200_OK,
        )


class ExperimentCopyView(APIView):
    """POST /experiment/<experiment_id>/copy — copia a análise para outro contexto.

    Zero trust: exige ``can_edit_experiment`` na origem (um viewer não exporta
    a estratégia de análise de outra pessoa) e membership ativa na
    organização de destino — ``organization_id`` nulo copia para o espaço
    pessoal do próprio usuário. A cópia reutiliza o blob físico (mesmo
    ``file``/``sha256``) mas cria linhas novas de amostras, subsamples e
    gates: editar uma não toca a outra.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=inline_serializer(
            name="ExperimentCopyRequest",
            fields={
                "title": serializers.CharField(required=False),
                "organization_id": serializers.IntegerField(
                    required=False, allow_null=True
                ),
            },
        ),
        responses={201: ListExperimentSerializer},
    )
    def post(self, request, experiment_id):
        source = get_object_or_404(
            experiments_visible_to(request.user), id=experiment_id
        )
        if not can_edit_experiment(request.user, source):
            return Response(
                {
                    "detail": "Copiar exige permissão de edição no experimento de origem."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        raw = request.data.get("organization_id")
        if raw in (None, ""):
            organization_id = None
        else:
            try:
                organization_id = int(raw)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "organization_id inválido."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not Organization.objects.filter(id=organization_id).exists():
                return Response(
                    {"detail": "Laboratório não encontrado."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not is_org_member(request.user, organization_id):
                return Response(
                    {"detail": "Você não é membro do laboratório de destino."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        title = request.data.get("title")
        if title is not None and (not isinstance(title, str) or not title.strip()):
            return Response(
                {"detail": "Título inválido."}, status=status.HTTP_400_BAD_REQUEST
            )

        clone = copy_experiment(
            source,
            user=request.user,
            title=title,
            organization_id=organization_id,
        )
        return Response(
            ListExperimentSerializer(clone).data, status=status.HTTP_201_CREATED
        )


class ExperimentDeriveAnalysisView(APIView):
    """POST /experiment/<experiment_id>/derive-analysis/ — deriva a estratégia
    de outro experimento sobre as amostras deste (BE-19, ADR-0021).

    Casa amostras por `content_guid` (fallback `file_name`) e clona as
    árvores de gates — snapshot, sem vínculo vivo e sem `copied_from`
    cross-experimento. Amostras com gates existentes são puladas. Opcional:
    encaixe em subsamples homônimos e cópia da matriz de compensação
    aplicada. Zero trust: exige edição nos dois experimentos.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=inline_serializer(
            name="ExperimentDeriveAnalysisRequest",
            fields={
                "source_experiment_id": serializers.IntegerField(),
                "include_subsamples": serializers.BooleanField(required=False),
                "include_compensation": serializers.BooleanField(required=False),
            },
        ),
        responses={200: inline_serializer(name="DeriveAnalysisResponse", fields={})},
    )
    def post(self, request, experiment_id):
        target = get_object_or_404(
            experiments_visible_to(request.user), id=experiment_id
        )
        if not can_edit_experiment(request.user, target):
            return Response(
                {"detail": "Derivar análise exige edição no experimento alvo."},
                status=status.HTTP_403_FORBIDDEN,
            )

        raw = request.data.get("source_experiment_id")
        try:
            source_id = int(raw)
        except (TypeError, ValueError):
            return Response(
                {"detail": "source_experiment_id é obrigatório."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if source_id == target.id:
            return Response(
                {"detail": "Origem e alvo são o mesmo experimento."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        source = get_object_or_404(experiments_visible_to(request.user), id=source_id)
        if not can_edit_experiment(request.user, source):
            return Response(
                {
                    "detail": "Derivar exige permissão de edição no experimento de origem."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        report = derive_analysis(
            target,
            source,
            user=request.user,
            include_subsamples=request.data.get("include_subsamples", True),
            include_compensation=request.data.get("include_compensation", True),
        )
        return Response(report, status=status.HTTP_200_OK)


class FileHashCheckView(APIView):
    """POST /experiment/check-hash/ — dedup de upload por SHA-256 (BE-12).

    O cliente calcula o hash localmente e pergunta se o blob já existe;
    existindo, o upload pode ser pulado e o blob reutilizado no
    ``complete/`` (``reuse: true``). Nunca bloqueia o upload — é aviso e
    conveniência de storage, não portão de permissão.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=inline_serializer(
            name="FileHashCheckRequest",
            fields={"sha256": serializers.CharField()},
        ),
        responses=inline_serializer(
            name="FileHashCheckResponse",
            fields={
                "exists": serializers.BooleanField(),
                "file_name": serializers.CharField(allow_null=True),
            },
        ),
    )
    def post(self, request):
        sha256 = request.data.get("sha256")
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(c not in "0123456789abcdef" for c in sha256.lower())
        ):
            return Response(
                {"detail": "sha256 deve ser um hash hexadecimal de 64 caracteres."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Dedup escopado por experimento quando `experiment_id` vem no body:
        # "este arquivo já está NESTE experimento". Sem ele, a resposta cobre
        # só os experimentos visíveis ao usuário — file_name de blob alheio
        # não vaza. O hash pode ser do blob (FileModel.sha256) ou de um
        # .fcs individual já extraído (FileDataModel.content_sha256).
        experiment_id = request.data.get("experiment_id")
        visible = experiments_visible_to(request.user)
        blob_qs = FileModel.objects.filter(
            sha256=sha256.lower(), experiment__in=visible
        )
        sample_qs = FileDataModel.objects.filter(
            content_sha256=sha256.lower(), experiment__in=visible
        )
        if experiment_id not in (None, ""):
            blob_qs = blob_qs.filter(experiment_id=experiment_id)
            sample_qs = sample_qs.filter(experiment_id=experiment_id)

        blob = blob_qs.order_by("id").first()
        if blob is not None:
            return Response(
                {"exists": True, "file_name": blob.file_name},
                status=status.HTTP_200_OK,
            )
        sample = sample_qs.order_by("id").first()
        return Response(
            {
                "exists": sample is not None,
                "file_name": sample.file_name if sample else None,
            },
            status=status.HTTP_200_OK,
        )


class ExperimentFileInitView(APIView):
    """POST /experiment/<experiment_id>/files/init — upload anexado a um
    experimento existente ("adicionar arquivos").

    Cria o ``FileModel`` reserva (sem ``file`` ainda) e devolve seu id como
    ``fileId``; os chunks seguem em ``/experiment/files/upload-chunk/``.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ExperimentFileInitSerializer,
        responses=inline_serializer(
            name="ExperimentFileInitResponse",
            fields={"fileId": serializers.IntegerField()},
        ),
    )
    def post(self, request, experiment_id):
        experiment = get_object_or_404(
            experiments_visible_to(request.user), id=experiment_id
        )
        require_can_edit_experiment(request.user, experiment)

        serializer = ExperimentFileInitSerializer(data=request.data)
        if not serializer.is_valid():
            return _invalid(serializer)

        data = serializer.validated_data
        extension = experiment_file_extension(data["fileName"])
        upload = FileModel.objects.create(
            experiment=experiment,
            file_name=data["fileName"],
            total_chunks=data["totalChunks"],
        )
        return Response({"fileId": upload.id, "extension": extension}, status=201)


class ExperimentFileChunkView(APIView):
    """POST /experiment/files/upload-chunk/ — chunk do upload de arquivo.

    Mesmo protocolo do upload de experimento, mas o ``fileId`` é o id do
    ``FileModel`` reserva e os ``.part`` são namespaced por ``f<id>``.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ChunkUploadSerializer,
        responses=inline_serializer(
            name="ChunkStatusResponse",
            fields={"status": serializers.CharField()},
        ),
    )
    def post(self, request):
        serializer = ChunkUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return _invalid(serializer)

        upload = get_object_or_404(
            uploads_visible_to(request.user).select_related("experiment"),
            id=serializer.validated_data["fileId"],
        )
        require_can_edit_experiment(request.user, upload.experiment)

        chunk_index = serializer.validated_data["chunkIndex"]
        chunk = serializer.validated_data["chunk"]

        chunk_dir = os.path.join(settings.MEDIA_ROOT, "chunks")
        os.makedirs(chunk_dir, exist_ok=True)
        chunk_path = os.path.join(chunk_dir, f"f{upload.id}_{chunk_index}.part")
        with open(chunk_path, "wb") as f:
            for c in chunk.chunks():
                f.write(c)

        if chunk_index not in upload.received_chunks:
            upload.received_chunks.append(chunk_index)
            upload.save(update_fields=["received_chunks"])

        return Response({"status": "ok"})


class ExperimentFileCompleteView(APIView):
    """POST /experiment/files/complete/ — monta e extrai o upload anexado.

    `.fcs` solto é aglutinado num ZIP; amostras já presentes no experimento
    (mesmo ``content_guid``/``content_sha256``) são puladas e reportadas.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=ExperimentCompleteSerializer,
        responses=inline_serializer(
            name="ExperimentFileCompleteResponse",
            fields={
                "status": serializers.CharField(),
                "added": serializers.IntegerField(),
                "skipped": serializers.ListField(child=serializers.CharField()),
            },
        ),
    )
    def post(self, request):
        serializer = ExperimentCompleteSerializer(data=request.data)
        if not serializer.is_valid():
            return _invalid(serializer)

        upload = get_object_or_404(
            uploads_visible_to(request.user).select_related("experiment"),
            id=serializer.validated_data["fileId"],
        )
        experiment = upload.experiment
        require_can_edit_experiment(request.user, experiment)

        raw_file_name = serializer.validated_data.get("fileName") or upload.file_name
        try:
            extension = experiment_file_extension(raw_file_name)
        except DjangoValidationError as exc:
            return Response(
                {"detail": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST
            )

        upload_key = f"f{upload.id}"
        try:
            final_path = assemble_chunks(upload_key, upload.total_chunks, extension)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        if extension == ".fcs":
            zip_path = os.path.join(settings.MEDIA_ROOT, f"{upload_key}.zip")
            final_path = wrap_fcs_as_zip(
                final_path, raw_file_name or upload_key, zip_path
            )

        try:
            upload.sha256 = file_sha256(final_path)
        except OSError:
            upload.sha256 = None
        upload.file = final_path
        upload.file_name = raw_file_name
        upload.save(update_fields=["file", "file_name", "sha256"])

        skipped: list = []
        try:
            extract_metadata_from_zip(upload, skipped=skipped)
        except Exception as e:
            logger.error(
                "Erro ao extrair metadados do upload %s: %s",
                upload.id,
                e,
                exc_info=True,
            )
            return Response(
                {"status": "error", "detail": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        added = FileDataModel.objects.filter(file=upload).count()
        return Response(
            {"status": "done", "added": added, "skipped": skipped},
            status=status.HTTP_200_OK,
        )


class ExperimentDownloadView(APIView):
    """GET /experiment/<experiment_id>/download — ZIP reconstruído por subsample.

    O blob guardado é o freezer; a saída é artefato derivado: cada amostra
    ativa sai em ``<subsample>/<arquivo>.fcs`` (raiz quando sem subsample),
    refletindo a organização atual do usuário — independente de em qual
    upload/ZIP o `.fcs` originalmente veio.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, experiment_id):
        experiment = get_object_or_404(
            experiments_visible_to(request.user), id=experiment_id
        )
        files = (
            FileDataModel.objects.filter(experiment=experiment, active=True)
            .select_related("subsample", "file")
            .order_by("subsample__name", "file_name")
        )

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp.close()
        used_names: set = set()
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as out:
            for fd in files:
                data = self._read_fcs_bytes(fd)
                if data is None:
                    continue
                folder = f"{fd.subsample.name}/" if fd.subsample else ""
                arcname = self._unique_name(folder + fd.file_name, used_names)
                out.writestr(arcname, data)

        handle = open(tmp.name, "rb")
        response = FileResponse(
            handle,
            as_attachment=True,
            filename=f"{experiment.title}.zip",
        )

        def _cleanup():
            handle.close()
            os.unlink(tmp.name)

        response._resource_closers.append(_cleanup)
        return response

    @staticmethod
    def _read_fcs_bytes(fd: FileDataModel) -> bytes | None:
        """Lê o `.fcs` da amostra no ZIP do upload dela (ou fcs_path legado)."""
        upload_file = getattr(getattr(fd.file, "file", None), "path", None)
        entry = fd.source_path or fd.file_name
        if upload_file and os.path.exists(upload_file):
            try:
                with zipfile.ZipFile(upload_file, "r") as zf:
                    names = zf.namelist()
                    match = [n for n in names if n == entry] or [
                        n for n in names if n.endswith(f"/{entry}")
                    ]
                    if match:
                        return zf.read(match[0])
            except (zipfile.BadZipFile, KeyError, OSError):
                pass
        if fd.fcs_path and os.path.exists(fd.fcs_path):
            with open(fd.fcs_path, "rb") as f:
                return f.read()
        return None

    @staticmethod
    def _unique_name(name: str, used: set) -> str:
        if name not in used:
            used.add(name)
            return name
        base, ext = os.path.splitext(name)
        i = 2
        while f"{base}_{i}{ext}" in used:
            i += 1
        candidate = f"{base}_{i}{ext}"
        used.add(candidate)
        return candidate


class TagListCreateView(generics.ListCreateAPIView):
    """GET/POST /experiment/tags/ — vocabulário de tags (BE-34).

    GET lista o vocabulário visível: tags de sistema + das
    organizações do usuário + pessoais dele. POST cria tag de
    usuário: com ``organization`` → escopo organização (exige
    membership ativa), sem → pessoal. ``system_key`` e
    ``category="control"`` são só de sistema — a API nunca cria.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = SampleTagSerializer

    def get_queryset(self):
        from fcs_parser.services.tags import tags_visible_to

        queryset = tags_visible_to(self.request.user)
        category = self.request.query_params.get("category")
        if category:
            queryset = queryset.filter(category=category)
        return queryset.order_by("category", Lower("name"))

    def create(self, request, *args, **kwargs):
        from fcs_parser.services.tags import tags_visible_to

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        organization = serializer.validated_data.get("organization")
        scope = SampleTagModel.SCOPE_PERSONAL
        if organization is not None:
            if not is_org_member(request.user, organization.id):
                raise PermissionDenied("Você não é membro ativo deste laboratório.")
            scope = SampleTagModel.SCOPE_ORGANIZATION

        # Dedup por escopo: nome normalizado já existe → devolve a
        # existente (criar tag é idempotente para o mesmo vocabulário).
        name_normalized = SampleTagModel.normalize(serializer.validated_data["name"])
        existing = tags_visible_to(request.user).filter(name_normalized=name_normalized)
        existing = (
            existing.filter(organization=organization)
            if organization
            else existing.filter(created_by=request.user)
        ).first()
        if existing:
            return Response(
                SampleTagSerializer(existing).data,
                status=status.HTTP_200_OK,
            )

        try:
            with transaction.atomic():
                tag = serializer.save(
                    scope=scope,
                    organization=organization,
                    created_by=request.user,
                    system_key=None,
                    category=SampleTagModel.CATEGORY_GENERAL,
                )
        except IntegrityError:
            # Race entre a checagem e o insert: a existente venceu.
            raise serializers.ValidationError(
                {"name": "Já existe uma tag com este nome neste escopo."}
            )
        return Response(SampleTagSerializer(tag).data, status=status.HTTP_201_CREATED)


class TagDetailView(generics.RetrieveUpdateAPIView):
    """PATCH /experiment/tags/<id>/ — renomeia/recolore tag de usuário.

    Tags de sistema são imutáveis por API (só migration). A tag
    inativa some do vocabulário mas mantém vínculos (A API nunca
    deleta) — por isso não há DELETE aqui: inativação é via PATCH
    ``active=false`` quando houver caso de uso.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = SampleTagSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        from fcs_parser.services.tags import tags_visible_to

        return tags_visible_to(self.request.user)

    def perform_update(self, serializer):
        from fcs_parser.services.tags import can_edit_tag

        if not can_edit_tag(self.request.user, serializer.instance):
            raise PermissionDenied("Tags de sistema não podem ser alteradas pela API.")
        serializer.save()


class FileTagsView(APIView):
    """PUT /experiment/file/<file_id>/tags — define as tags da amostra.

    Substituição completa do conjunto (payload = ids). A exclusividade
    de controle e a visibilidade são garantidas por ``set_file_tags``,
    o único caminho de escrita (BE-34).
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=FileTagsUpdateSerializer,
        responses=ListFileDataSerializer,
    )
    def put(self, request, file_id):
        from fcs_parser.services.tags import set_file_tags

        file_data = get_object_or_404(
            file_data_visible_to(request.user).select_related("experiment"),
            id=file_id,
        )
        require_can_edit_file_data(request.user, file_data)

        serializer = FileTagsUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return _invalid(serializer)

        before = sorted(file_data.tags.values_list("id", flat=True))
        tags = set_file_tags(file_data, serializer.validated_data["tags"], request.user)
        after = sorted(tag.id for tag in tags)
        if before != after:
            names = ", ".join(tag.name for tag in tags) or "—"
            record_revision(
                experiment=file_data.experiment,
                action=AnalysisRevision.ACTION_TAGS,
                target_type=AnalysisRevision.TARGET_FILE,
                target_id=file_data.id,
                user=request.user,
                payload_before={"targets": {str(file_data.id): {"tags": before}}},
                payload_after={"targets": {str(file_data.id): {"tags": after}}},
                affected_ids=[file_data.id],
                summary=(f'etiquetou a amostra "{file_data.file_name}": {names}'),
            )

        return Response(
            ListFileDataSerializer(file_data).data, status=status.HTTP_200_OK
        )
