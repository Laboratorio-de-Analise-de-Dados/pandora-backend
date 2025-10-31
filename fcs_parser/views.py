import json
import os
import traceback
from django.conf import settings
import pandas as pd
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from analytics.models import GateModel
from fcs_parser.tasks import process_experiment_files_task
from rest_framework.views import  Response, status
from rest_framework import generics
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


class ExperimentListCreateView(SerializerByMethodMixin, generics.ListCreateAPIView):
    serializer_map = {"GET": ListExperimentSerializer, "POST": ExperimentSerializer}
    queryset = ExperimentModel.objects.all()

    def post(self, request):
        serializer = ExperimentSerializer(data={**request.POST, **request.FILES})
        if serializer.is_valid():
            title = serializer.validated_data.get("title").replace(" ", "_")
            file = serializer.validated_data.get("file")
            experiment_type = serializer.validated_data.get("type")
            try:
                experiment_instance, created = ExperimentModel.objects.get_or_create(
                    title=title, type=experiment_type
                )
                file_instance = FileModel.objects.create(
                    file=file, file_name=file.name, experiment=experiment_instance
                ) 
                process_experiment_files_task.delay(file_instance.id)
                return Response(
                    model_to_dict(experiment_instance), status=status.HTTP_201_CREATED
                )
            except Exception as e:
                return Response(
                    {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


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
        dataset = pd.DataFrame(file_data.data_set)
        dataset.columns = dataset.columns.str.replace(" ", "")
        dataset.columns = dataset.columns.str.replace("-", "_")
        dataset.columns = dataset.columns.str.lower()
        dataset = dataset.head(limit)
        file_data.data_set = json.loads(dataset.to_json(orient="records"))
        serializer = self.serializer_class(file_data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class ProcessFileDataView(generics.CreateAPIView):

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
            file_data_models = []
            try:
                for root, dirs, files in os.walk(directory_path): 
                    for file_name in files:
                        if file_name.endswith(".fcs"):
                            complete_path: str = os.path.join(root, file_name)
                            processed_file = process_fcs_file(complete_path)
                            if len(values) == 0:
                                values = processed_file[2]
                            file_data_model = FileDataModel(
                                headers=processed_file[0],
                                data_set=processed_file[1],
                                experiment=experiment,
                                file_name=file_name,
                                file=file
                            )
                            file_data_models.append(file_data_model)
                            if len(file_data_models) == 10:
                                FileDataModel.objects.bulk_create(file_data_models)
                                file_data_models = []
                if len(file_data_models) > 0:
                    FileDataModel.objects.bulk_create(file_data_models)
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
