"""Rotas /internal/* — serviço Juvia (BE-27).

Não publicar no nginx: essas rotas só existem na pandora_net. A auth de
serviço (`InternalServiceView`) é a defesa em profundidade.
"""

from django.urls import path

from .views_internal import (
    InternalFileEventsView,
    InternalJobClaimView,
    InternalJobCompleteView,
    InternalJobFailView,
)

app_name = "internal"

urlpatterns = [
    path("jobs/claim", InternalJobClaimView.as_view(), name="job-claim"),
    path(
        "files/<int:file_id>/events",
        InternalFileEventsView.as_view(),
        name="file-events",
    ),
    path(
        "jobs/<int:job_id>/complete",
        InternalJobCompleteView.as_view(),
        name="job-complete",
    ),
    path(
        "jobs/<int:job_id>/fail",
        InternalJobFailView.as_view(),
        name="job-fail",
    ),
]
