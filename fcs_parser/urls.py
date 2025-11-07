from django.urls import path

from .views import (
    ExperimentCompleteView,
    ExperimentInitView,
    ExperimentListView,
    GetExperimentFiles,
    ListFileParams,
    ProcessFileDataView,
    RetrieveDeleteExperimentView,
    UploadChunkView,
)

app_name = "fcs_parse"

urlpatterns = [
    path("", ExperimentListView.as_view()),
    path("list/data/<str:experiment_id>/", GetExperimentFiles.as_view()),
    path("file/<str:file_id>/list", ListFileParams.as_view()),
    path("<str:experiment_id>/", RetrieveDeleteExperimentView.as_view()),
    path("file/<int:file_id>/process", ProcessFileDataView.as_view()),
    path("init/", ExperimentInitView.as_view(), name="experiment-init"),
    path("upload-chunk/", UploadChunkView.as_view(), name="upload-chunk"),
    path("complete/", ExperimentCompleteView.as_view(), name="experiment-complete")
]
