from django.urls import path
from .views import ExperimentCreateView, GetExperimentFiles, ListExperimentView, ListFileParams
app_name = 'fcs_parse'

urlpatterns = [
    path('list/', ListExperimentView.as_view()),
    path('create/', ExperimentCreateView.as_view()),
    path('list/data/<str:experiment_id>/', GetExperimentFiles.as_view()),
    path('file/<str:file_id>/list', ListFileParams.as_view())
]