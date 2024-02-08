from django.urls import path
from .views import process_zip

app_name = 'fcs_parse'

urlpatterns = [
    path('parse/', process_zip, name='process_zip'),
]