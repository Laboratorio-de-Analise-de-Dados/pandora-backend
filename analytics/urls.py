from django.urls import path

from .views import (
    ApplyGateView,
    CreateGateView,
    GateDensityView,
    GetGateDataView,
    UpdateGateView,
)

app_name = "analytics"

urlpatterns = [
    path("gate", CreateGateView.as_view()),
    path("gate/apply", ApplyGateView.as_view()),
    path("gate/<int:gate_id>", UpdateGateView.as_view()),
    path("gate/<int:gate_id>/list", GetGateDataView.as_view()),
    path("gate/<int:gate_id>/density", GateDensityView.as_view()),
]
