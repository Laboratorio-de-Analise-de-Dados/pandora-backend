"""Leitura de compensação (spillover) embutida nos headers FCS — BE-22 v1.

FCS 3.x guarda a matriz na keyword ``$SPILLOVER`` como lista
``n,ch1,...,chn,v11..vnn`` (valores em row-major); arquivos antigos usam
``$COMP`` no mesmo formato. Os headers gravados em ``FileDataModel``
são os keywords crus do ``readfcs`` — a busca de chave é
case-insensitive por segurança.

Só detecção/leitura: persistir como ``CompensationMatrix`` e aplicar
``S^-1`` na leitura são as próximas etapas do PRD (ADR-0018/0019).
"""

from __future__ import annotations

import re

SPILLOVER_KEYS = ("$spillover", "$comp")


def parse_spillover(headers: dict | None) -> dict | None:
    """Extrai a matriz embutida dos headers de um .fcs.

    Retorna ``{"channels": [...], "matrix": [[...]]}`` ou ``None`` quando
    a keyword não existe ou está malformada (matriz descartada em vez de
    quebrar a leitura — arquivo malformado não é erro de API).
    """
    if not isinstance(headers, dict):
        return None

    raw = None
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() in SPILLOVER_KEYS:
            raw = value
            break
    if raw is None:
        return None

    # O valor pode vir como string "n,ch1,...,vnn" ou já como lista.
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        return None

    if not parts:
        return None
    try:
        n = int(float(parts[0]))
    except (TypeError, ValueError):
        return None
    if n < 1 or len(parts) < 1 + n + n * n:
        return None

    channels = [str(p) for p in parts[1 : n + 1]]
    try:
        values = [float(p) for p in parts[1 + n : 1 + n + n * n]]
    except (TypeError, ValueError):
        return None

    matrix = [values[i * n : (i + 1) * n] for i in range(n)]
    return {"channels": channels, "matrix": matrix}


def headers_have_spillover(headers: dict | None) -> bool:
    return parse_spillover(headers) is not None


SPILLOVER_KEY_PATTERN = re.compile(r"^\$?(spillover|comp)$", re.IGNORECASE)


def experiment_embedded_compensation(experiment) -> dict | None:
    """Matriz embutida da primeira amostra ativa que a tiver.

    Todas as amostras do experimento normalmente vêm do mesmo instrumento
    e compartilham a matriz — a primeira parseável representa o conjunto.
    """
    from fcs_parser.models import FileDataModel

    for fd in (
        FileDataModel.objects.filter(experiment=experiment, active=True)
        .order_by("id")
        .only("id", "headers")
    ):
        parsed = parse_spillover(fd.headers)
        if parsed is not None:
            return {**parsed, "file_data_id": fd.id, "source": "fcs_header"}
    return None
