
from django.shortcuts import get_object_or_404
from fcs_parser.serializers import ListFileDataSerializer, ParamListDataSerializer
from rest_framework import generics
from analytics.models import GateModel
from analytics.serializers import DashboardSerializer, GateSerializer, ListGateSerializer
from fcs_parser.models import FileDataModel
from utils.mixins import SerializerByMethodMixin
from rest_framework.views import  Response, status
import pandas as pd
import json

# Create your views here.

class CreateListGateView(SerializerByMethodMixin, generics.ListCreateAPIView):
    serializer_map = {"GET": ListGateSerializer, "POST": GateSerializer}

    def get_queryset(self):
        file_id = self.kwargs['file_id']
        return GateModel.objects.filter(file_data_id=file_id)
    
    def get(self, request, *args, **kwargs):
      file_id = self.kwargs['file_id']
      
      file_data = get_object_or_404(FileDataModel, pk=file_id)
  
      tree = {
          "id": file_data.id,
          "file_name": file_data.file_name,
          "data_set": file_data.data_set,
          "gates": [],
      }

      gates = self.get_queryset().values(
          "id", "name", "parent_id", "gate_coordinates"
      )

      gate_map = {}
      for gate in gates:
          gate_map[gate["id"]] = {**gate, "children": []}

      for gate in gates:
          parent_id = gate["parent_id"]
          if parent_id:
              gate_map[parent_id]["children"].append(gate_map[gate["id"]])
          else:
              tree["gates"].append(gate_map[gate["id"]])
    
      return Response(tree, status=status.HTTP_200_OK)
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
# class CreateDashboardView(generics.CreateAPIView):
