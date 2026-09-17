"""Compensação (spillover) — BE-22, ADR-0018/0019.

Leitura da matriz embutida: FCS 3.x guarda-a na keyword ``$SPILLOVER``
como lista ``n,ch1,...,chn,v11..vnn`` (valores em row-major); arquivos
antigos usam ``$COMP`` no mesmo formato. Os headers gravados em
``FileDataModel`` são os keywords crus do ``readfcs`` — a busca de chave
é case-insensitive por segurança.

Aplicação: o dado bruto nunca é reescrito (ADR-0004) — na leitura, os
eventos dos canais cobertos são multiplicados por ``S⁻¹`` antes do
binning/escala. Cálculo: medianas dos controles single-stain contra o
negativo (unstained), com pool das réplicas do subsample-controle.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from utils.density import default_scale, normalize_column_name

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


# --- Aplicação na leitura (ADR-0018) -------------------------------------


def fluorescent_channels(experiment) -> list[str]:
    """Canais de fluorescência do experimento (exclui FSC/SSC/Time)."""
    return [v for v in (experiment.values or []) if v and default_scale(v) != "linear"]


def apply_compensation(
    dataset: pd.DataFrame, channels: list, matrix: list
) -> pd.DataFrame:
    """Multiplica os eventos dos canais cobertos por ``S⁻¹`` (espaço cru).

    Canal da matriz ausente no dataset → submatriz dos presentes é
    invertida (padrão flowCore); canal ausente na matriz fica intacto.
    Matriz singular cai em pseudo-inversa em vez de quebrar a leitura.
    """
    if dataset.empty or not channels or not matrix:
        return dataset

    norm = [normalize_column_name(c) for c in channels]
    idx = [i for i, c in enumerate(norm) if c in dataset.columns]
    if not idx:
        return dataset

    cols = [norm[i] for i in idx]
    spill = np.asarray(matrix, dtype=float)
    sub = spill[np.ix_(idx, idx)]
    try:
        inv = np.linalg.inv(sub)
    except np.linalg.LinAlgError:
        inv = np.linalg.pinv(sub)

    out = dataset.copy()
    out.loc[:, cols] = (inv @ dataset[cols].to_numpy(dtype=float).T).T
    return out


def applied_compensation(experiment):
    """A matriz ativa do experimento, ou None."""
    from analytics.models import CompensationMatrix

    return CompensationMatrix.objects.filter(
        experiment=experiment, is_applied=True, active=True
    ).first()


def compensate_dataset(dataset: pd.DataFrame, experiment):
    """Aplica a matriz ativa do experimento, se houver.

    Retorna ``(dataset, matrix_id)`` — ``matrix_id=None`` quando não há
    compensação aplicada (a view usa na chave de cache).
    """
    applied = applied_compensation(experiment)
    if applied is None:
        return dataset, None
    return apply_compensation(dataset, applied.channels, applied.matrix), applied.id


# --- Controles e cálculo (ADR-0019) --------------------------------------


def experiment_controls(experiment):
    """Controles marcados via subsamples: ``(unstained_ids, {canal: ids})``.

    Devolve ids de ``FileDataModel`` ativos — os arquivos de um
    subsample-controle são réplicas e entram juntos no pool.
    """
    from fcs_parser.models import SubsampleModel

    unstained: list[int] = []
    stains: dict[str, list[int]] = {}
    subsamples = SubsampleModel.objects.filter(
        experiment=experiment, active=True, control_type__isnull=False
    ).prefetch_related("files")
    for sub in subsamples:
        file_ids = [f.id for f in sub.files.all() if f.active]
        if not file_ids:
            continue
        if sub.control_type == sub.CONTROL_UNSTAINED:
            unstained.extend(file_ids)
        elif sub.control_type == sub.CONTROL_SINGLE_STAIN and sub.control_channel:
            stains.setdefault(sub.control_channel, []).extend(file_ids)
    return unstained, stains


def _pooled_median(file_ids: list[int]) -> pd.Series | None:
    """Mediana por canal fazendo pool dos eventos das réplicas."""
    from fcs_parser.models import FileDataModel

    frames = []
    for fd in FileDataModel.objects.filter(id__in=file_ids, active=True):
        df = fd.get_dataframe()
        if not df.empty:
            df = df.copy()
            df.columns = [normalize_column_name(c) for c in df.columns]
            frames.append(df)
    if not frames:
        return None
    pooled = pd.concat(frames, ignore_index=True)
    return pooled.median(numeric_only=True)


def compute_spillover_matrix(
    negative_ids: list[int], stains: dict[str, list[int]]
) -> dict:
    """S[i][j] = (MFI_i(controle_j) − MFI_i(neg)) / (MFI_j(controle_j) − MFI_j(neg)).

    MFI = mediana dos eventos no canal. ``stains`` mapeia nome do canal
    (como no subsample) → ids de réplicas. Levanta ``ValueError`` com a
    mensagem de domínio quando faltam dados ou o denominador zera.
    """
    if not negative_ids:
        raise ValueError("Compensação exige um controle negativo (unstained).")
    if len(stains) < 2:
        raise ValueError("Compensação exige ao menos dois controles single-stain.")

    neg_med = _pooled_median(negative_ids)
    if neg_med is None:
        raise ValueError("Controle negativo não tem eventos.")

    channels = list(stains.keys())
    norm_channels = [normalize_column_name(c) for c in channels]
    missing = [c for c, n in zip(channels, norm_channels) if n not in neg_med.index]
    if missing:
        raise ValueError(f"Controle negativo não tem os canais: {', '.join(missing)}.")

    n = len(channels)
    matrix = [[0.0] * n for _ in range(n)]
    medians = {}
    for channel, ids in stains.items():
        med = _pooled_median(ids)
        if med is None:
            raise ValueError(f"Controle de {channel} não tem eventos.")
        medians[channel] = med

    for j, channel in enumerate(channels):
        j_norm = norm_channels[j]
        med_j = medians[channel]
        denom = float(med_j.get(j_norm, np.nan)) - float(neg_med[j_norm])
        if not np.isfinite(denom) or abs(denom) < 1e-9:
            raise ValueError(
                f"Controle de {channel} não separa do negativo "
                "(MFI do canal ≈ MFI do unstained)."
            )
        for i, i_norm in enumerate(norm_channels):
            mfi_i = float(med_j.get(i_norm, np.nan))
            if not np.isfinite(mfi_i):
                raise ValueError(
                    f"Controle de {channel} não tem o canal {channels[i]}."
                )
            matrix[i][j] = (mfi_i - float(neg_med[i_norm])) / denom

    return {"channels": channels, "matrix": matrix}


# --- Apply/remove: revisão + invalidação + recálculo ---------------------


def _switch_applied(experiment, matrix) -> None:
    """Troca a flag `is_applied` sem revisão/recálculo (uso interno e do
    revert do histórico — a revisão ali é a ACTION_REVERT, não uma
    compensation_apply dupla)."""
    from analytics.models import CompensationMatrix

    CompensationMatrix.objects.filter(experiment=experiment, is_applied=True).update(
        is_applied=False
    )
    if matrix is not None:
        CompensationMatrix.objects.filter(pk=matrix.pk).update(is_applied=True)


def refresh_analysis_views(experiment) -> None:
    """Invalida densidade e recalcula gates raiz após trocar a matriz."""
    from analytics.tasks import recalculate_gate_analysis
    from fcs_parser.models import FileDataModel
    from utils.density import invalidate_density

    for fd in FileDataModel.objects.filter(experiment=experiment, active=True):
        invalidate_density(fd.id)
        for gate in fd.gates.filter(parent__isnull=True):
            recalculate_gate_analysis(gate.id)


def set_applied_compensation(experiment, matrix, user) -> None:
    """Liga (`matrix`) ou desliga (`None`) a compensação do experimento.

    Atômico: desmarca a anterior, marca a nova e grava a revisão
    (BE-08) — a mudança altera resultados de gates, então entra na
    timeline. Depois invalida o cache de densidade de cada amostra e
    recalcula as estatísticas dos gates (a geometria fica — o gate é
    interpretado no espaço exibido, compensado ou não).
    """
    from django.db import transaction

    from analytics.history import record_revision
    from analytics.models import AnalysisRevision, CompensationMatrix

    previous = CompensationMatrix.objects.filter(
        experiment=experiment, is_applied=True
    ).first()
    if (previous is None and matrix is None) or (
        previous is not None and matrix is not None and previous.id == matrix.id
    ):
        return  # nada mudou — sem revisão nem recálculo

    applying = matrix is not None
    target = matrix if applying else previous
    with transaction.atomic():
        _switch_applied(experiment, matrix)
        record_revision(
            experiment=experiment,
            action=(
                AnalysisRevision.ACTION_COMPENSATION_APPLY
                if applying
                else AnalysisRevision.ACTION_COMPENSATION_REMOVE
            ),
            target_type=AnalysisRevision.TARGET_COMPENSATION,
            target_id=target.id,
            user=user,
            payload_before={
                "targets": {
                    str(experiment.id): {
                        "compensation": previous.id if previous else None
                    }
                }
            },
            payload_after={
                "targets": {
                    str(experiment.id): {
                        "compensation": matrix.id if applying else None
                    }
                }
            },
            affected_ids=[experiment.id],
            summary=(
                f'aplicou a compensação "{target.name or target.id}"'
                if applying
                else "removeu a compensação aplicada"
            ),
        )

    refresh_analysis_views(experiment)
