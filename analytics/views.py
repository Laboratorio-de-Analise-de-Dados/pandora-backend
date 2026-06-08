
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from fcs_parser.serializers import ParamListDataSerializer
from rest_framework import generics, serializers
from rest_framework.views import APIView, Response, status
from analytics.models import GateModel
from analytics.serializers import DashboardSerializer, GateSerializer
from utils.density import (
    DEFAULT_COFACTOR,
    apply_gate_filter,
    compute_density,
    default_scale,
    density_cache_key,
    get_cached_density,
    normalize_columns,
    parse_range,
    set_cached_density,
    subsample_scatter,
)
import pandas as pd
import json

# Create your views here.

class CreateGateView(generics.CreateAPIView):
    serializer_class = GateSerializer

    def post(self, request, *args, **kwargs):
        data = request.data.copy()
        dashboard_data = data.pop('dashboard', None)

        dashboard_serializer = DashboardSerializer(data=dashboard_data)
        dashboard_serializer.is_valid(raise_exception=True)

        dash_instance = dashboard_serializer.save()
        data['dashboard'] = dash_instance.id
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class GetGateDataView(generics.ListAPIView):
    serializer_class = GateSerializer
    lookup_url_kwarg = "gate_id"

    def get_object(self):
        gate_id = self.kwargs.get(self.lookup_url_kwarg)
        return get_object_or_404(GateModel, pk=gate_id)
    
    def _apply_gate_filter(self, dataset: pd.DataFrame, gate: GateModel) -> pd.DataFrame:
        """Aplica o filtro de um gate (retangulo ou poligono) ao dataset.

        Delega ao helper compartilhado (utils.density.apply_gate_filter), que
        trata retangulo e poligono de forma vetorizada.
        """
        return apply_gate_filter(dataset, gate)


    def get(self, request, *args, **kwargs):
              
        target_gate = self.get_object()
        
        current_gate = target_gate
        gate_path = [current_gate] 

        while current_gate.parent:
            current_gate = current_gate.parent
            gate_path.insert(0, current_gate) 

        root_gate = gate_path[0]
        file_data_instance = root_gate.file_data

        dataset = file_data_instance.get_dataframe()

        dataset.columns = dataset.columns.str.replace(" ", "")
        dataset.columns = dataset.columns.str.replace("-", "_")
        dataset.columns = dataset.columns.str.lower()

        for gate_in_path in gate_path:
            dataset = self._apply_gate_filter(dataset, gate_in_path)
            if dataset.empty:
                print(f"Dataset vazio após filtrar pelo gate '{gate_in_path.name}'. Parando processamento.")
                break
        limit = request.query_params.get("limit", 10000)
        try:
            limit = int(limit)
        except ValueError:
            limit = 10000 
        dataset = dataset.head(limit)
        file_data_instance.data_set = json.loads(dataset.to_json(orient="records"))
        serializer = ParamListDataSerializer(file_data_instance)
        return Response(serializer.data, status=status.HTTP_200_OK) 
class GateDensityView(APIView):
    """Return density (heatmap) or subsampled scatter for a gate's filtered data."""

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
            name="GateDensityResponse",
            fields={
                "mode": serializers.CharField(),
                "total_events": serializers.IntegerField(),
                "x_label": serializers.CharField(),
                "y_label": serializers.CharField(),
            },
        ),
    )
    def get(self, request, gate_id):
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

        gate = get_object_or_404(GateModel, pk=gate_id)

        cache_key = density_cache_key(
            "gate", gate.file_data_id, gate_id, x_param, y_param, mode, bins, sample,
            x_scale, y_scale, cofactor, cutoff,
        )
        if x_range:
            cache_key += f":xr{x_range[0]}:{x_range[1]}"
        if y_range:
            cache_key += f":yr{y_range[0]}:{y_range[1]}"
        cached = get_cached_density(cache_key)
        if cached is not None:
            return Response(cached, status=status.HTTP_200_OK)

        current = gate
        gate_path = [current]
        while current.parent:
            current = current.parent
            gate_path.insert(0, current)

        file_data = gate_path[0].file_data
        dataset = normalize_columns(file_data.get_dataframe())

        for g in gate_path:
            dataset = apply_gate_filter(dataset, g)
            if dataset.empty:
                break

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
