from django.urls import path

from .views import (
    ApplyGateView,
    CheckpointDetailView,
    CheckpointListCreateView,
    CheckpointRestoreView,
    CreateGateView,
    DeleteGateBatchView,
    ExperimentHistoryView,
    GateDensityView,
    GetGateDataView,
    HistoryDetailView,
    HistoryRestoreView,
    HistoryRevertView,
    HistoryStateView,
    UpdateGateView,
)

app_name = "analytics"

urlpatterns = [
    path("gate", CreateGateView.as_view()),
    path("gate/apply", ApplyGateView.as_view()),
    path("gate/delete-batch", DeleteGateBatchView.as_view()),
    path("gate/<int:gate_id>", UpdateGateView.as_view()),
    path("gate/<int:gate_id>/list", GetGateDataView.as_view()),
    path("gate/<int:gate_id>/density", GateDensityView.as_view()),
    path(
        "experiment/<int:experiment_id>/history/",
        ExperimentHistoryView.as_view(),
        name="experiment-history",
    ),
    path(
        "experiment/<int:experiment_id>/history/<int:revision_id>/restore/",
        HistoryRestoreView.as_view(),
        name="history-restore",
    ),
    path(
        "experiment/<int:experiment_id>/checkpoints/",
        CheckpointListCreateView.as_view(),
        name="experiment-checkpoints",
    ),
    path(
        "history/<int:revision_id>/",
        HistoryDetailView.as_view(),
        name="history-detail",
    ),
    path(
        "history/<int:revision_id>/revert/",
        HistoryRevertView.as_view(),
        name="history-revert",
    ),
    path(
        "history/<int:revision_id>/state/",
        HistoryStateView.as_view(),
        name="history-state",
    ),
    path(
        "checkpoints/<int:pk>/",
        CheckpointDetailView.as_view(),
        name="checkpoint-detail",
    ),
    path(
        "checkpoints/<int:pk>/restore/",
        CheckpointRestoreView.as_view(),
        name="checkpoint-restore",
    ),
]
