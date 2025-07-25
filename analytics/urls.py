from django.urls import path

from .views import (
    CreateListGateView,
    GetGateDataView,
)

app_name = "analytics"

urlpatterns = [
    path("gate", CreateListGateView.as_view()),
    path("gate/<int:gate_id>/list", GetGateDataView.as_view()),
]
