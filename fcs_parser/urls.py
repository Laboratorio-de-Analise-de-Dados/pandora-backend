from django.urls import path

from .views import (
    ExperimentCompleteView,
    ExperimentInitView,
    ExperimentListView,
    FileDensityView,
    GetExperimentFiles,
    ListFileParams,
    ProcessFileDataView,
    RecomputeFileDataView,
    RetrieveDeleteExperimentView,
    UploadChunkView,
)

app_name = "fcs_parse"

urlpatterns = [
    path("init/", ExperimentInitView.as_view()),
    path("upload-chunk/", UploadChunkView.as_view()),
    path("complete/", ExperimentCompleteView.as_view()),
    path("list/data/<str:experiment_id>/", GetExperimentFiles.as_view()),
    path("file/<str:file_id>/list", ListFileParams.as_view()),
    path("file/<int:file_id>/density", FileDensityView.as_view()),
    path("file/<int:file_id>/recompute", RecomputeFileDataView.as_view()),
    path("<str:experiment_id>/", RetrieveDeleteExperimentView.as_view()),
    path("file/<int:file_id>/process", ProcessFileDataView.as_view()),
    path("", ExperimentListView.as_view())
]
