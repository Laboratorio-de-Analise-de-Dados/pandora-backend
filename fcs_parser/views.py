import json
import os
import shutil
import traceback
from django.conf import settings
import pandas as pd
from pathlib import Path
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from analytics.models import GateModel
from fcs_parser.tasks import process_experiment_files_task, recompute_file_data_task
from drf_spectacular.utils import extend_schema, inline_serializer, OpenApiParameter
from rest_framework.response import Response
from rest_framework import status
from rest_framework import generics, serializers
from rest_framework.views import APIView
from utils.density import (
    DEFAULT_COFACTOR,
    compute_density,
    default_scale,
    density_cache_key,
    get_cached_density,
    invalidate_density,
    normalize_columns,
    parse_range,
    set_cached_density,
    subsample_scatter,
)
from fcs_parser.models import ExperimentModel, FileDataModel, FileModel
from fcs_parser.serializers import (
    ExperimentSerializer,
    ListExperimentSerializer,
    ListFileDataSerializer,

    ParamListDataSerializer,
)
from django.forms.models import model_to_dict

from fcs_parser.services.decompressor import decompres_file
from fcs_parser.services.process_fcs import process_fcs_file
from utils.mixins import SerializerByMethodMixin


class ExperimentInitView(generics.CreateAPIView):
    @extend_schema(
        request=inline_serializer(
            name="ExperimentInitRequest",
            fields={
                "title": serializers.CharField(),
                "type": serializers.CharField(),
                "totalChunks": serializers.IntegerField(),
            },
        ),
        responses=inline_serializer(
            name="ExperimentInitResponse",
            fields={"fileId": serializers.CharField()},
        ),
    )
    def post(self, request):
        title = request.data.get("title").replace(" ", "_")
        experiment_type = request.data.get("type")
        total=request.data.get("totalChunks")
        experiment = ExperimentModel.objects.create(
            title=title,
            type=experiment_type,
            status="uploading",
            file_status="uploading",
            total_chunks=total,
        )
        return Response({"fileId": str(experiment.id)}, status=201)

class UploadChunkView(generics.CreateAPIView):
    @extend_schema(
        request=inline_serializer(
            name="UploadChunkRequest",
            fields={
                "fileId": serializers.CharField(),
                "chunkIndex": serializers.IntegerField(),
                "chunk": serializers.FileField(),
            },
        ),
        responses=inline_serializer(
            name="StatusResponse",
            fields={"status": serializers.CharField()},
        ),
    )
    def post(self, request):
        file_id = request.data["fileId"]
        chunk_index = int(request.data["chunkIndex"])
        chunk = request.FILES["chunk"]

        experiment = ExperimentModel.objects.get(id=file_id)

        # salva em disco
        upload_dir = Path("/tmp/uploads")
        upload_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = upload_dir / f"{file_id}_{chunk_index}.part"
        with open(chunk_path, "wb") as f:
            for c in chunk.chunks():
                f.write(c)

        # atualiza progresso
        if chunk_index not in experiment.received_chunks:
            experiment.received_chunks.append(chunk_index)
            experiment.save(update_fields=["received_chunks"])

        return Response({"status": "ok"})
    
class ExperimentCompleteView(generics.CreateAPIView):
    @extend_schema(
        request=inline_serializer(
            name="ExperimentCompleteRequest",
            fields={"fileId": serializers.CharField()},
        ),
        responses=inline_serializer(
            name="ExperimentCompleteResponse",
            fields={"status": serializers.CharField()},
        ),
    )
    def post(self, request):
        file_id = request.data["fileId"]
        experiment = ExperimentModel.objects.get(id=file_id)

        upload_dir = Path("/tmp/uploads")
        final_name = f"{file_id}.zip"
        final_path = Path(settings.MEDIA_ROOT) / final_name

        with open(final_path, "wb") as outfile:
            for i in range(experiment.total_chunks):
                chunk_path = upload_dir / f"{file_id}_{i}.part"
                if not chunk_path.exists():
                    raise ValueError(f"Chunk {i} faltando")
                with open(chunk_path, "rb") as f:
                    outfile.write(f.read())
                os.remove(chunk_path)

        experiment.file_status = "uploaded"
        experiment.status = "new"
        experiment.save(update_fields=["file_status", "status"])

        # cria o FileModel apontando para o zip final
        file_instance = FileModel.objects.create(
            file=str(final_path),   # ou usar um campo FileField com upload_to
            file_name=final_name,
            experiment=experiment
        )

        # dispara task assíncrona com apenas o file_id
        process_experiment_files_task.delay(file_instance.id)

        return Response({"status": "processing"})

    
class ExperimentListView(generics.ListAPIView):
    serializer_class = ListExperimentSerializer
    queryset = ExperimentModel.objects.all()

    # def post(self, request):
    #     serializer = ExperimentSerializer(data={**request.POST, **request.FILES})
    #     if serializer.is_valid():
    #         title = serializer.validated_data.get("title").replace(" ", "_")
    #         file = serializer.validated_data.get("file")
    #         experiment_type = serializer.validated_data.get("type")
    #         try:
    #             experiment_instance, created = ExperimentModel.objects.get_or_create(
    #                 title=title, type=experiment_type
    #             )
    #             file_instance = FileModel.objects.create(
    #                 file=file, file_name=file.name, experiment=experiment_instance
    #             ) 
    #             process_experiment_files_task.delay(file_instance.id)
    #             return Response(
    #                 model_to_dict(experiment_instance), status=status.HTTP_201_CREATED
    #             )
    #         except Exception as e:
    #             return Response(
    #                 {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
    #             )
    #     return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class RetrieveDeleteExperimentView(generics.RetrieveDestroyAPIView):
    lookup_url_kwarg = "experiment_id"
    serializer_class = ListExperimentSerializer
    queryset = ExperimentModel.objects.all()

    def perform_destroy(self, instance):
        return instance.delete()


class GetExperimentFiles(generics.ListAPIView):
    lookup_url_kwarg = "experiment_id"
    serializer_class = ListFileDataSerializer

    def get_queryset(self):
        experiment_id = self.kwargs.get("experiment_id")
        queryset = FileDataModel.objects.filter(experiment_id=experiment_id)
        return queryset
    def list(self, request, *args, **kwargs):
        # Retrieve the queryset of files
        queryset = self.get_queryset()

        # Manually create the response data with the gate tree
        data = []
        for file in queryset:
            # Get the flat serialized data for the file
            file_serializer = self.get_serializer(file)
            file_data = file_serializer.data

            # Build the gate tree for the current file
            gate_tree = GateModel.build_tree(file_data_id=file.id)

            # Add the built tree to the file data
            file_data['gates'] = gate_tree
            data.append(file_data)
        
        return Response(data)

class ListFileParams(generics.ListAPIView):
    lookup_field = "file_id"
    serializer_class = ParamListDataSerializer

    def get_queryset(self):
        file_id = self.kwargs.get("file_id")
        queryset = get_object_or_404(FileDataModel, id=file_id)
        return queryset

    def list(self, request, *args, **kwargs):
        limit = request.query_params.get("limit", 10000)
        try:
            limit = int(limit)
        except ValueError:
            limit = 10000

        file_data = self.get_queryset()
        dataset = file_data.get_dataframe()
        dataset.columns = dataset.columns.str.replace(" ", "")
        dataset.columns = dataset.columns.str.replace("-", "_")
        dataset.columns = dataset.columns.str.lower()
        dataset = dataset.head(limit)
        file_data.data_set = json.loads(dataset.to_json(orient="records"))
        serializer = self.serializer_class(file_data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class FileDensityView(APIView):
    """Return density (heatmap) or subsampled scatter for a file's data."""

    @extend_schema(
        parameters=[
            OpenApiParameter(name="x", type=str, required=True, description="X-axis parameter (e.g. FSC-A)"),
            OpenApiParameter(name="y", type=str, required=True, description="Y-axis parameter (e.g. SSC-A)"),
            OpenApiParameter(name="mode", type=str, required=False, description="'heatmap' (default) or 'scatter'"),
            OpenApiParameter(name="bins", type=int, required=False, description="Bins for heatmap (default 200)"),
            OpenApiParameter(name="sample", type=int, required=False, description="Max points for scatter (default 5000)"),
            OpenApiParameter(name="xscale", type=str, required=False, description="'linear' or 'biex' (default: heuristic by channel)"),
            OpenApiParameter(name="yscale", type=str, required=False, description="'linear' or 'biex' (default: heuristic by channel)"),
            OpenApiParameter(name="cofactor", type=float, required=False, description="arcsinh cofactor for biex (default 150)"),
            OpenApiParameter(name="cutoff", type=int, required=False, description="Heatmap density cutoff: bins with count <= cutoff become null/transparent (default 0)"),
            OpenApiParameter(name="xmin", type=float, required=False, description="Lower bound for X axis (raw value)"),
            OpenApiParameter(name="xmax", type=float, required=False, description="Upper bound for X axis (raw value)"),
            OpenApiParameter(name="ymin", type=float, required=False, description="Lower bound for Y axis (raw value)"),
            OpenApiParameter(name="ymax", type=float, required=False, description="Upper bound for Y axis (raw value)"),
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
            "file", file_id, file_id, x_param, y_param, mode, bins, sample,
            x_scale, y_scale, cofactor, cutoff,
        )
        if x_range:
            cache_key += f":xr{x_range[0]}:{x_range[1]}"
        if y_range:
            cache_key += f":yr{y_range[0]}:{y_range[1]}"
        cached = get_cached_density(cache_key)
        if cached is not None:
            return Response(cached, status=status.HTTP_200_OK)

        file_data = get_object_or_404(FileDataModel, id=file_id)
        dataset = normalize_columns(file_data.get_dataframe())

        base = {"mode": mode, "total_events": len(dataset), "x_label": x_param, "y_label": y_param}

        if mode == "scatter":
            result = subsample_scatter(dataset, x_param, y_param, sample, x_scale, y_scale, cofactor, x_range, y_range)
        else:
            result = compute_density(dataset, x_param, y_param, bins, x_scale, y_scale, cofactor, cutoff, x_range, y_range)

        if result is None:
            return Response(
                {"detail": f"Columns '{x_param}' or '{y_param}' not found in dataset."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = {**base, **result}
        set_cached_density(cache_key, payload)
        return Response(payload, status=status.HTTP_200_OK)


class ProcessFileDataView(generics.CreateAPIView):

    @extend_schema(
        request=None,
        responses=inline_serializer(
            name="ProcessFileDataResponse",
            fields={"message": serializers.CharField()},
        ),
    )
    def post(self, request, *args, **kwargs):
        file_id = kwargs.get('file_id')
        file = get_object_or_404(FileModel, id=file_id)
        experiment = file.experiment
        if experiment.status != 'processing':
            experiment.status = 'processing'
            experiment.save()
            directory_path = os.path.join(settings.MEDIA_ROOT, "fcs_files", file.file_name)
            file_path = os.path.join(settings.MEDIA_ROOT, file.file_name)
            os.makedirs(directory_path, exist_ok=True)
            decompres_file(file_path, directory_path)
            values = []
            fcs_source_dir = os.path.join(
                settings.MEDIA_ROOT, "fcs_source", str(experiment.id)
            )
            os.makedirs(fcs_source_dir, exist_ok=True)
            try:
                for root, dirs, files in os.walk(directory_path): 
                    for file_name in files:
                        if file_name.endswith(".fcs"):
                            complete_path: str = os.path.join(root, file_name)
                            processed_file = process_fcs_file(complete_path)
                            if isinstance(processed_file, str):
                                raise ValueError(processed_file)
                            if len(values) == 0:
                                values = processed_file[2]
                            persistent_fcs = os.path.join(fcs_source_dir, file_name)
                            shutil.copy2(complete_path, persistent_fcs)
                            file_data_model = FileDataModel.objects.create(
                                headers=processed_file[0],
                                data_set=None,
                                experiment=experiment,
                                file_name=file_name,
                                file=file,
                                fcs_path=persistent_fcs,
                            )
                            file_data_model.save_dataframe(
                                pd.DataFrame(processed_file[1])
                            )
                experiment.values = values
                experiment.status = 'done'
                experiment.save()

                return Response({"message": "File processing was successfull."}, status=status.HTTP_200_OK)

            except Exception as e:
                error_info = {
                    'error_message': str(e),
                    'details': traceback.format_exc()
                }
                experiment.status = 'error'
                experiment.error_info = error_info
                experiment.save()
        else:
            return Response({"message": "The file is still being processed."}, status=status.HTTP_400_BAD_REQUEST)


class RecomputeFileDataView(APIView):
    """Plano D: reprocessa um FileData a partir do .fcs original + gates.

    Dispara a regeneracao do Parquet (cache morno) e a invalidacao da density
    no Redis de forma assincrona (Celery).
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
        file_data = get_object_or_404(FileDataModel, id=file_id)
        if not file_data.fcs_path:
            return Response(
                {"detail": "Sem .fcs original armazenado para reprocessar este arquivo."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        recompute_file_data_task.delay(file_data.id)
        return Response(
            {"status": "scheduled", "file_data_id": file_data.id},
            status=status.HTTP_202_ACCEPTED,
        )
