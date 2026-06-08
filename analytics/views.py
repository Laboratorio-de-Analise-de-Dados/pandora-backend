
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from fcs_parser.serializers import ParamListDataSerializer
from rest_framework import generics, serializers
from rest_framework.views import APIView, Response, status
from analytics.models import GateModel
from analytics.serializers import DashboardSerializer, GateSerializer
from utils.density import apply_gate_filter, compute_density, normalize_columns, subsample_scatter
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
        """
        Aplica o filtro de coordenadas de um único gate ao dataset fornecido.
        Retorna o DataFrame filtrado.
        """
        gate_coords = gate.gate_coordinates
        
        x_label = 'fsc_a' 
        y_label = 'ssc_a' 

        if hasattr(gate, 'dashboard') and gate.dashboard and gate.dashboard.dashboard_config:
            dashboard_config = gate.dashboard.dashboard_config
            x_label = dashboard_config.get('x_axis_label', x_label).lower().replace(" ", "_").replace("-", "_")
            y_label = dashboard_config.get('y_axis_label', y_label).lower().replace(" ", "_").replace("-", "_")
        
        # Verifique se as colunas dos eixos existem no DataFrame
        if x_label not in dataset.columns or y_label not in dataset.columns:
            print(f"Aviso: Eixos '{x_label}' ou '{y_label}' não encontrados no dataset. Não foi possível aplicar filtro para gate '{gate.name}'.")
            return dataset # Retorna o dataset sem filtro se os eixos não existirem

        start_x = gate_coords.get('startX')
        end_x = gate_coords.get('endX')
        start_y = gate_coords.get('startY')
        end_y = gate_coords.get('endY')

        if all([start_x is not None, end_x is not None, start_y is not None, end_y is not None]):
            filtered_dataset = dataset[
                (dataset[x_label] >= start_x) & (dataset[x_label] <= end_x) &
                (dataset[y_label] >= start_y) & (dataset[y_label] <= end_y)
            ]
            print(f"Filtro aplicado para gate '{gate.name}': {len(dataset)} -> {len(filtered_dataset)} linhas.")
            return filtered_dataset
        else:
            print(f"Aviso: Coordenadas incompletas para gate '{gate.name}'. Não foi possível aplicar filtro. Coordenadas: {gate_coords}")
            return dataset 


    def get(self, request, *args, **kwargs):
              
        target_gate = self.get_object()
        
        current_gate = target_gate
        gate_path = [current_gate] 

        while current_gate.parent:
            current_gate = current_gate.parent
            gate_path.insert(0, current_gate) 

        root_gate = gate_path[0]
        file_data_instance = root_gate.file_data

        dataset = pd.DataFrame(file_data_instance.data_set)

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
        gate = get_object_or_404(GateModel, pk=gate_id)

        current = gate
        gate_path = [current]
        while current.parent:
            current = current.parent
            gate_path.insert(0, current)

        file_data = gate_path[0].file_data
        dataset = normalize_columns(pd.DataFrame(file_data.data_set))

        for g in gate_path:
            dataset = apply_gate_filter(dataset, g)
            if dataset.empty:
                break

        x_param = request.query_params.get("x", "FSC-A")
        y_param = request.query_params.get("y", "SSC-A")
        mode = request.query_params.get("mode", "heatmap")
        total_events = len(dataset)

        base = {"mode": mode, "total_events": total_events, "x_label": x_param, "y_label": y_param}

        if mode == "scatter":
            sample = int(request.query_params.get("sample", 5000))
            result = subsample_scatter(dataset, x_param, y_param, sample)
        else:
            bins = int(request.query_params.get("bins", 200))
            result = compute_density(dataset, x_param, y_param, bins)

        if result is None:
            return Response(
                {"detail": f"Columns '{x_param}' or '{y_param}' not found in dataset."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({**base, **result}, status=status.HTTP_200_OK)
