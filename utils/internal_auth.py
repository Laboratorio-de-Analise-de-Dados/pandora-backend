"""Autenticação de serviço para as rotas /internal/* (BE-27).

O Juvia (worker de ML) faz poll na fila do Pandora chamando essas rotas
com `Authorization: Bearer ${JUVIA_INTERNAL_TOKEN}` — token compartilhado
via env nos dois serviços, comparado em tempo constante. Vale nas duas
direções: o Juvia valida o mesmo token nas rotas dele (juvia ADR-0001).

Defesa em profundidade (o perímetro real é a rede interna): falha fechada
em qualquer direção — sem token configurado o endpoint recusa 503 em vez
de aceitar tráfego sem autenticação; header ausente/inválido → 401 sem
detalhe que vaze o mecanismo.
"""

import secrets

from django.conf import settings
from rest_framework.exceptions import APIException
from rest_framework.views import APIView


class InternalUnauthorized(APIException):
    """401 direto — `NotAuthenticated` do DRF vira 403 quando a view não
    tem authenticator pra gerar o header WWW-Authenticate (o caso aqui:
    `authentication_classes` vazio de propósito)."""

    status_code = 401
    default_detail = "Credencial de serviço ausente ou inválida."
    default_code = "not_authenticated"


class ServiceUnavailable(APIException):
    status_code = 503
    default_detail = "Serviço interno indisponível."
    default_code = "service_unavailable"


class Conflict(APIException):
    status_code = 409
    default_detail = "Conflito de estado."
    default_code = "conflict"


def check_internal_token(request) -> None:
    """Exige `Authorization: Bearer` com o token de serviço configurado.

    Lido do settings a cada chamada para `override_settings` funcionar nos
    testes sem reconfigurar a view.
    """
    expected = settings.JUVIA_INTERNAL_TOKEN
    if not expected:
        raise ServiceUnavailable()
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not token
        or not secrets.compare_digest(token, expected)
    ):
        raise InternalUnauthorized()


class InternalServiceView(APIView):
    """Base das rotas /internal/* — serviço chama serviço, sem usuário.

    `authentication_classes`/`permission_classes` vazios: essas rotas não
    aceitam credencial de usuário — só o Bearer de serviço conferido aqui.
    """

    authentication_classes = []
    permission_classes = []

    def initial(self, request, *args, **kwargs):
        check_internal_token(request)
        super().initial(request, *args, **kwargs)
