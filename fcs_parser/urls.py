from django.urls import path

from .views import (
    ExperimentListCreateView,
    GetExperimentFiles,
    ListFileParams,
    ProcessFileDataView,
    RetrieveDeleteExperimentView,
)

app_name = "fcs_parse"

urlpatterns = [
    path("", ExperimentListCreateView.as_view()),
    path("list/data/<str:experiment_id>/", GetExperimentFiles.as_view()),
    path("file/<str:file_id>/list", ListFileParams.as_view()),
    path("<str:experiment_id>/", RetrieveDeleteExperimentView.as_view()),
    path("file/<int:file_id>/process", ProcessFileDataView.as_view()),
]
