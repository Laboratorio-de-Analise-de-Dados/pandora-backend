

import os
import ipdb
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView, Response, status

from fcs_parser.models import ExperimentModel, FileDataModel
from fcs_parser.serializers import ExperimentSerializer
from fcs_parser.services.decompressor import decompres_file
from fcs_parser.services.process_fcs import process_fcs_file


class ExperimentCreateView(APIView):

    serializer_class = ExperimentSerializer 
    @csrf_exempt
    def post(self, request):
        serializer = ExperimentSerializer(data=request.data)
        if serializer.is_valid():
            title = serializer.validated_data.get('title').replace(' ', '_')
            file = serializer.validated_data.get('file')
            experiment_instance, created = ExperimentModel.objects.get_or_create(title=title)
            
            experiment_id = experiment_instance.id
            directory_path = os.path.join(settings.BASE_DIR, 'assets', 'fcs_files', title)
            os.makedirs(directory_path, exist_ok=True)
            decompres_file(file, directory_path)
            for file_name in os.listdir(directory_path):
                if file_name.endswith(".fcs"):
                    complete_path: str = os.path.join(directory_path, file_name)
                    headers, data_set = process_fcs_file(complete_path)
                    FileDataModel.objects.get_or_create(headers=headers, data_set=data_set, experiment_id=experiment_id)            
            
            # Serialize o experimento
            serialized_experiment = ExperimentSerializer(experiment_instance)
            return Response(serialized_experiment.data, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        
