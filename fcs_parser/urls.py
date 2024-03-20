from django.urls import path
from .views import ExperimentCreateView
app_name = 'fcs_parse'

urlpatterns = [
    path('create', ExperimentCreateView.as_view()),
]