from django.urls import path

from .views import (
    ApplyGateView,
    CreateGateView,
    DeleteGateBatchView,
    ExperimentHistoryView,
    GateDensityView,
    GetGateDataView,
    HistoryDetailView,
    HistoryRevertView,
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
        "history/<int:revision_id>/",
        HistoryDetailView.as_view(),
        name="history-detail",
    ),
    path(
        "history/<int:revision_id>/revert/",
        HistoryRevertView.as_view(),
        name="history-revert",
    ),
]
