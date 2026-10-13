"""Metadados por canal extraídos do header FCS ($PnN/$PnR/$PnE/$PnS/$PnG).

O header inteiro já é materializado em ``FileDataModel.headers`` no parse
do ZIP — aqui só interpretamos as keywords por parâmetro para o front
escolher escala e limites de eixo fiéis ao detector:

- ``$PnR`` — range nativo do canal (FACS antigo grava 262144, CytoFLEX
  costuma gravar 4194304+; é o teto real daquele detector);
- ``$PnE`` — "décadas,offset" de log na aquisição (0 = canal linear);
- ``$PnS`` / ``$PnG`` — nome do stain e gain, quando presentes.
"""

from __future__ import annotations

import re

from fcs_parser.services.controls import _header_value

_PNE_DECADES = re.compile(r"^\s*([-\d.]+)")

# Varredura máxima quando $PAR vem ausente/ilegível — para na primeira
# lacuna de $PnN, então é só um teto de segurança.
_MAX_CHANNELS_SCAN = 128


def _as_number(raw: str):
    """Int quando inteiro (JSON mais limpo), float senão."""
    value = float(raw)
    return int(value) if value == int(value) else value


def parse_channel_meta(headers: dict | None) -> list[dict]:
    """Por-canal, ordenado pelo índice do parâmetro ($P1..$Pn).

    Devolve ``[]`` quando o header não traz keywords por canal — o front
    cai nos limites/escalas default globais.
    """
    if not headers:
        return []
    par_raw = _header_value(headers, "PAR")
    try:
        par = int(float(par_raw)) if par_raw else 0
    except ValueError:
        par = 0
    limit = par if par > 0 else _MAX_CHANNELS_SCAN
    meta: list[dict] = []
    for i in range(1, limit + 1):
        name = _header_value(headers, f"P{i}N")
        if not name:
            if par <= 0:
                break
            continue
        entry: dict = {"name": name}
        stain = _header_value(headers, f"P{i}S")
        if stain:
            entry["stain"] = stain
        rng = _header_value(headers, f"P{i}R")
        if rng:
            try:
                entry["range"] = _as_number(rng)
            except ValueError:
                pass
        pne = _header_value(headers, f"P{i}E")
        if pne:
            match = _PNE_DECADES.match(pne)
            if match:
                try:
                    entry["log_decades"] = float(match.group(1))
                except ValueError:
                    pass
        gain = _header_value(headers, f"P{i}G")
        if gain:
            try:
                entry["gain"] = _as_number(gain)
            except ValueError:
                pass
        meta.append(entry)
    return meta


def parse_cytometer(headers: dict | None) -> str | None:
    """Modelo do citômetro por arquivo (`$CYT`/`$CYTSN`) — metadado
    informativo do instrumento que adquiriu a amostra (ADR-0031)."""
    return _header_value(headers, "CYT", "CYTSN")
