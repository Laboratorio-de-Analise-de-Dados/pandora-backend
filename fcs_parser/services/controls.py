"""BE-26: pistas de contexto de controle por amostra (arquivo).

Funções puras — filename + keywords do header FCS já materializado em
``FileDataModel.headers`` → `well_id`, `plate` e `suggested_control`.

Heurística é **informativa**: nunca é aplicada sozinha — o front
pré-marca e o usuário confirma via PATCH em lote. Gate de
positividade precisa distinguir FMO/isotípico/unstained — sugerir o
tipo errado aqui vira erro silencioso na derivação de gates.
"""

from __future__ import annotations

import re

# Vocabulário de controle por amostra (BE-26). Distinto do subsample
# (BE-22 só tem unstained/single_stain — réplicas de compensação):
# aqui cobre os papéis que a derivação de gates precisa distinguir.
CONTROL_UNSTAINED = "unstained"
CONTROL_SINGLE_STAIN = "single_stain"
CONTROL_FMO = "fmo"
CONTROL_ISOTYPE = "isotype"
CONTROL_BEADS = "beads"
CONTROL_BIOLOGICAL = "biological"

CONTROL_TYPE_CHOICES = [
    (CONTROL_UNSTAINED, "Negativo (unstained)"),
    (CONTROL_SINGLE_STAIN, "Single stain"),
    (CONTROL_FMO, "FMO"),
    (CONTROL_ISOTYPE, "Isotípico"),
    (CONTROL_BEADS, "Beads"),
    (CONTROL_BIOLOGICAL, "Controle biológico"),
]

# Tipos que exigem canal fluorescente marcado.
CONTROL_TYPES_WITH_CHANNEL = {
    CONTROL_SINGLE_STAIN,
    CONTROL_FMO,
    CONTROL_ISOTYPE,
    CONTROL_BEADS,
}

_WELL_RE = re.compile(r"_?([A-H])(0?[1-9]|1[0-2])\b")

# Padrões de sugestão: (regex no texto, control_type). Ordem importa —
# a primeira que casa vence ("single stain" antes de "beads" etc.).
_SUGGEST_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"unstained|unstain|negativ|_neg\b|\bneg[_\s]"), CONTROL_UNSTAINED),
    (re.compile(r"\bfmo\b"), CONTROL_FMO),
    (re.compile(r"isotyp|iso[_\s]?type|isocontrol"), CONTROL_ISOTYPE),
    (re.compile(r"single[_\s]?stain|singlestain"), CONTROL_SINGLE_STAIN),
    (re.compile(r"\bbeads?\b|\bcomp[_\s]?bead"), CONTROL_BEADS),
    (
        re.compile(r"\bcomp\b|compensation|controle?[_\s]|\bcontrol\b"),
        CONTROL_BIOLOGICAL,
    ),
]

# Fluoróforos comuns → canal sugerido quando o filename o menciona.
_FLUOROPHORES = [
    r"fitc",
    r"alexa[_\s]?488",
    r"pe",
    r"percp",
    r"pe[_\s]?cy5",
    r"pe[_\s]?cy7",
    r"apc",
    r"alexa[_\s]?647",
    r"bv421",
    r"bv510",
    r"bv605",
    r"bv711",
    r"pacific[_\s]?blue",
    r"v500",
]


def _norm_header_key(key: str) -> str:
    """'$WELL ID' / 'WELLID' / 'well_id' → 'WELLID' — casa variações."""
    return re.sub(r"[\s_$]+", "", str(key)).upper()


def _header_value(headers: dict | None, *candidates: str) -> str | None:
    if not headers:
        return None
    wanted = {_norm_header_key(c) for c in candidates}
    for key, value in headers.items():
        if _norm_header_key(key) in wanted and value:
            return str(value).strip()
    return None


def parse_well_id(headers: dict | None, file_name: str) -> str | None:
    """Poço da amostra (`A1`…`H12`).

    Fonte 1: header FCS (`WELL ID`, `TUBE NAME` — placas BD/FACSDiva).
    Fonte 2: regex no `file_name` (`Specimen_001_A1_A01.fcs` → `A1`).
    """
    raw = _header_value(headers, "WELL ID", "WELLID", "WELL", "TUBE NAME")
    if raw:
        match = _WELL_RE.search(raw.upper())
        if match:
            return f"{match.group(1)}{int(match.group(2))}"
        return raw

    name = (file_name or "").upper()
    matches = _WELL_RE.findall(name)
    if matches:
        letter, number = matches[-1]  # último poço no nome (mais específico)
        return f"{letter}{int(number)}"
    return None


def parse_plate(headers: dict | None) -> str | None:
    """Nome/ID da placa do header — sinal de "experimento de placa"."""
    return _header_value(headers, "PLATE NAME", "PLATENAME", "PLATE ID", "PLATEID")


def _fluorophore_in(text: str) -> str | None:
    for fluor in _FLUOROPHORES:
        if re.search(rf"\b{fluor}\b", text):
            return re.sub(r"[_\s]", "", fluor).upper()
    return None


def suggest_control(file_name: str, headers: dict | None) -> dict | None:
    """Heurística de sugestão de controle — filename + keywords.

    Retorna ``{control_type, control_channel}`` ou ``None`` quando
    nada sugere controle. `control_channel` é o fluoróforo citado no
    texto (quando há); a validação real de canal acontece no PATCH.
    """
    text = " ".join(
        part
        for part in [
            (file_name or ""),
            _header_value(headers, "TUBE NAME", "TUBENAME", "SAMPLE ID", "SRC") or "",
        ]
        if part
    ).lower()
    # `_`/`-`/`.` são separadores em filename, não letras — sem isso
    # `\bfmo\b` não casa em `fmo_cd3_fitc.fcs` (underscore é \w).
    text = re.sub(r"[_\-.]+", " ", text)

    for pattern, control_type in _SUGGEST_RULES:
        if pattern.search(text):
            channel = (
                _fluorophore_in(text)
                if control_type in CONTROL_TYPES_WITH_CHANNEL
                else None
            )
            return {"control_type": control_type, "control_channel": channel}
    return None
