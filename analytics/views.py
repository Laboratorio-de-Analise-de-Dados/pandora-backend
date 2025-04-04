
from django.shortcuts import get_object_or_404
from rest_framework import generics
from analytics.models import GateModel
from analytics.serializers import GateSerializer, ListGateSerializer
from fcs_parser.models import FileDataModel
from utils.mixins import SerializerByMethodMixin
from rest_framework.views import  Response, status
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
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(file_data_id=self.kwargs['file_id'])
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
# class CreateDashboardView(generics.CreateAPIView):
