

import json
import os
import ipdb
import pandas as pd
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView, Response, status
from rest_framework import generics
from fcs_parser.models import ExperimentModel, FileDataModel
from fcs_parser.serializers import ExperimentSerializer, ListExperimentSerializer, ListFileDataSerializer, ParamListDataSerializer
from fcs_parser.services.decompressor import decompres_file
from fcs_parser.services.process_fcs import process_fcs_file


class ExperimentCreateView(APIView):

    serializer_class = ExperimentSerializer 
    @csrf_exempt
    def post(self, request):
        serializer = ExperimentSerializer(data=request.data)
        try:
            if serializer.is_valid():
                title = serializer.validated_data.get('title').replace(' ', '_')
                file = serializer.validated_data.get('file')
                
                directory_path = os.path.join(settings.BASE_DIR, 'assets', 'fcs_files', title)
                os.makedirs(directory_path, exist_ok=True)
                decompres_file(file, directory_path)
                for file_name in os.listdir(directory_path):
                    if file_name.endswith(".fcs"):
                        complete_path: str = os.path.join(directory_path, file_name)
                        processed_file = process_fcs_file(complete_path)
                        experiment_instance, created = ExperimentModel.objects.get_or_create(title=title, values=processed_file[2])
                        experiment_id = experiment_instance.id
                        FileDataModel.objects.get_or_create(headers=processed_file[0], data_set=processed_file[1], experiment_id=experiment_id, file_name=file_name)            
                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)    
        except:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ListExperimentView(generics.ListAPIView):
    serializer_class = ListExperimentSerializer

    def get_queryset(self):
        queryset = ExperimentModel.objects.all()
        return queryset
        
class GetExperimentFiles(generics.ListAPIView):
    lookup_url_kwarg = "experiment_id"
    serializer_class = ListFileDataSerializer

    def get_queryset(self):
        experiment_id = self.kwargs.get('experiment_id')
        queryset = FileDataModel.objects.filter(experiment_id=experiment_id)

        return queryset
        
class ListFileParams(generics.ListAPIView):
    lookup_field = "file_id"
    serializer_class = ParamListDataSerializer

    def get_queryset(self):
        file_id = self.kwargs.get('file_id')
        queryset = get_object_or_404(FileDataModel, id=file_id)
        return queryset
    
    def list(self, request, *args, **kwargs):
        x_axis = request.query_params.get('x_axis', 'SSC-A')
        y_axis = request.query_params.get('y_axis', 'FSC-A')
        file_data = self.get_queryset()
        dataset = pd.DataFrame(file_data.data_set)
        params = ['id', x_axis, y_axis]
        dataset = dataset[params]
        dataset.columns = dataset.columns.str.replace(' ', '')
        dataset.columns = dataset.columns.str.replace('-', '_')
        dataset.columns = dataset.columns.str.lower()
        file_data.data_set = json.loads(dataset.to_json(orient='records'))
        serializer = self.serializer_class(file_data)
        return Response(serializer.data, status=status.HTTP_200_OK)