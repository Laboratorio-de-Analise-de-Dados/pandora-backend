import json
import pandas as pd
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView, Response, status
from rest_framework import generics
from fcs_parser.models import ExperimentModel, FileDataModel, FileModel
from fcs_parser.serializers import (
    ExperimentSerializer,
    ListExperimentSerializer,
    ListFileDataSerializer,
    ParamListDataSerializer,
)
from django.forms.models import model_to_dict


class ExperimentCreateView(APIView):

    serializer_class = ExperimentSerializer

    @csrf_exempt
    def post(self, request):
        serializer = ExperimentSerializer(data=request.data)
        if serializer.is_valid():
            title = serializer.validated_data.get("title").replace(" ", "_")
            file = serializer.validated_data.get("file")
            experiment_type = serializer.validated_data.get("type")
            try:
                experiment_instance, created = ExperimentModel.objects.get_or_create(
                    title=title, type=experiment_type
                )
                FileModel.objects.create(
                    file=file, file_name=file.name, experiment=experiment_instance
                )
                return Response(
                    model_to_dict(experiment_instance), status=status.HTTP_201_CREATED
                )
            except Exception as e:
                return Response(
                    {"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ListExperimentView(generics.ListAPIView):
    serializer_class = ListExperimentSerializer
    queryset = ExperimentModel.objects.all()


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


class ListFileParams(generics.ListAPIView):
    lookup_field = "file_id"
    serializer_class = ParamListDataSerializer

    def get_queryset(self):
        file_id = self.kwargs.get("file_id")
        queryset = get_object_or_404(FileDataModel, id=file_id)
        return queryset

    def list(self, request, *args, **kwargs):
        x_axis = request.query_params.get("x_axis", "SSC-A")
        y_axis = request.query_params.get("y_axis", "FSC-A")
        file_data = self.get_queryset()
        dataset = pd.DataFrame(file_data.data_set)
        params = ["id", x_axis, y_axis]
        dataset = dataset[params]
        dataset.columns = dataset.columns.str.replace(" ", "")
        dataset.columns = dataset.columns.str.replace("-", "_")
        dataset.columns = dataset.columns.str.lower()
        file_data.data_set = json.loads(dataset.to_json(orient="records"))
        serializer = self.serializer_class(file_data)
        return Response(serializer.data, status=status.HTTP_200_OK)
