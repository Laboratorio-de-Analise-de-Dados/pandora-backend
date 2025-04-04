from django.urls import path

from .views import (
    CreateListGateView,
)

app_name = "analytics"

urlpatterns = [
    path("gate", CreateListGateView.as_view()),
]
