from django.urls import path

from .views import (
    DisableFileDataView,
    EnableFileDataView,
    ExperimentCompleteView,
    ExperimentInitView,
    ExperimentListView,
    FileDensityView,
    FileStatsView,
    FileSubsampleView,
    GetExperimentFiles,
    ListFileParams,
    ProcessFileDataView,
    RecomputeFileDataView,
    RetrieveDeleteExperimentView,
    SubsampleDetailView,
    SubsampleListCreateView,
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
    path("file/<int:file_id>/stats", FileStatsView.as_view()),
    path("file/<int:file_id>/disable", DisableFileDataView.as_view()),
    path("file/<int:file_id>/enable", EnableFileDataView.as_view()),
    path(
        "file/<int:file_id>/subsample",
        FileSubsampleView.as_view(),
        name="file_subsample",
    ),
    path(
        "<int:experiment_id>/subsamples/",
        SubsampleListCreateView.as_view(),
        name="subsample_list_create",
    ),
    path(
        "<int:experiment_id>/subsamples/<int:pk>/",
        SubsampleDetailView.as_view(),
        name="subsample_detail",
    ),
    path("<str:experiment_id>/", RetrieveDeleteExperimentView.as_view()),
    path("file/<int:file_id>/process", ProcessFileDataView.as_view()),
    path("", ExperimentListView.as_view())
]
