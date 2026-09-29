"""Filtros de gate sobre DataFrames de eventos.

Domínio de `analytics`: estas funções conhecem a estrutura de
`GateModel.gate_coordinates`/`dashboard` e são consumidas pelo recálculo de
análise (`analytics.tasks`), pelas views de stats/density e pelo preview de
compensação (`fcs_parser` importa aqui — a direção inversa já existe no
ciclo documentado fcs_parser ↔ analytics).

`utils.density` fica com o genérico (escala biex/linear, histogramas, cache);
o que é específico de gate vive aqui. Duck-typing proposital: o `gate` recebido
pode ser um `GateModel` ou um objeto simples de teste com os mesmos campos.
"""

import os
import re

import numpy as np
import pandas as pd

from utils.density import normalize_column_name


def _points_in_polygon(xs, ys, vertices) -> np.ndarray:
    """Teste vetorizado de ponto-em-poligono (ray casting). Coordenadas e
    vertices no MESMO espaco (cru). Retorna mascara booleana alinhada a xs/ys."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    poly = np.asarray(vertices, dtype=float)
    n = len(poly)
    inside = np.zeros(len(xs), dtype=bool)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i, 0], poly[i, 1]
        xj, yj = poly[j, 0], poly[j, 1]
        # Aresta cruza a linha horizontal do ponto?
        cond = ((yi > ys) != (yj > ys)) & (
            xs < (xj - xi) * (ys - yi) / (yj - yi + 1e-12) + xi
        )
        inside ^= cond
        j = i
    return inside


def gate_axis_channels(gate) -> tuple[str | None, str | None]:
    """Canais (crus, como gravados) que o gate referencia nos eixos X e Y.

    Mesma resolução de `apply_gate_filter`: `x_axis`/`y_axis` gravados em
    `gate_coordinates` têm precedência em qualquer tipo de gate; os labels
    do dashboard são apenas o fallback quando a coordenada não os carrega.
    Gate de intervalo não tem eixo Y — retorna `(x, None)`.
    """
    coords = gate.gate_coordinates or {}

    x_label = "FSC-A"
    y_label = "SSC-A"
    dash = getattr(gate, "dashboard", None)
    if dash is not None and dash.dashboard_config:
        config = dash.dashboard_config
        x_label = config.get("x_axis_label") or x_label
        y_label = config.get("y_axis_label") or y_label

    x_col = coords.get("x_axis") or x_label
    y_col = coords.get("y_axis") or y_label
    return (x_col, None) if coords.get("type") == "interval" else (x_col, y_col)


def missing_gate_channels(gate, columns) -> list[str]:
    """Canais que o gate referencia e que não existem nas colunas do dataset.

    Lista vazia = gate avaliável. Os nomes voltam crus (para exibição); a
    comparação com as colunas é normalizada.
    """
    cols = {normalize_column_name(c) for c in columns}
    missing = []
    for channel in gate_axis_channels(gate):
        if channel and normalize_column_name(channel) not in cols:
            missing.append(channel)
    return missing


def file_data_channels(file_data) -> set[str]:
    """Canais (normalizados) disponíveis na amostra, sem carregar os eventos.

    Ordem: schema do Parquet (só o footer) → keywords $PnN do header FCS →
    `get_dataframe()` como último recurso (rebuild custoso a partir do ZIP).
    """
    if file_data.parquet_path and os.path.exists(file_data.parquet_path):
        try:
            import pyarrow.parquet as pq

            names = pq.read_schema(file_data.parquet_path).names
            return {normalize_column_name(c) for c in names}
        except Exception:
            pass

    headers = file_data.headers or {}
    if isinstance(headers, dict):
        channels = {
            normalize_column_name(str(v))
            for k, v in headers.items()
            if isinstance(k, str) and re.fullmatch(r"\$?p\d+n", k) and v
        }
        if channels:
            return channels

    dataset = file_data.get_dataframe()
    return {normalize_column_name(c) for c in dataset.columns}


def apply_gate_filter(dataset: pd.DataFrame, gate) -> pd.DataFrame:
    """Apply a single gate's filter (rectangle, polygon, interval or quadrant) to a DataFrame.

    Colunas ja normalizadas; coordenadas do gate sempre em espaco cru (linear),
    independente da escala usada para exibir o grafico.
    """
    gate_coords = gate.gate_coordinates

    x_label = "fsc_a"
    y_label = "ssc_a"

    if (
        hasattr(gate, "dashboard")
        and gate.dashboard
        and gate.dashboard.dashboard_config
    ):
        config = gate.dashboard.dashboard_config
        x_label = normalize_column_name(config.get("x_axis_label", x_label))
        y_label = normalize_column_name(config.get("y_axis_label", y_label))

    gate_type = gate_coords.get("type")

    # `x_axis`/`y_axis` gravados no gate_coordinates têm precedência para
    # qualquer tipo; os labels do dashboard são o fallback quando o gate
    # não carrega os eixos (gates antigos, por exemplo).
    x_col = normalize_column_name(gate_coords.get("x_axis") or "") or x_label
    y_col = normalize_column_name(gate_coords.get("y_axis") or "") or y_label

    # Gate de intervalo (1D, apenas eixo X — histograma).
    if gate_type == "interval":
        if x_col not in dataset.columns:
            return dataset
        start_x = gate_coords.get("startX")
        end_x = gate_coords.get("endX")
        if start_x is not None and end_x is not None:
            return dataset[(dataset[x_col] >= start_x) & (dataset[x_col] <= end_x)]
        return dataset

    # Gate de quadrante: filtra um dos 4 quadrantes a partir do centro da cruz.
    if gate_type == "quadrant":
        if x_col not in dataset.columns or y_col not in dataset.columns:
            return dataset
        cx = gate_coords.get("center_x")
        cy = gate_coords.get("center_y")
        quadrant = gate_coords.get("quadrant")
        if cx is None or cy is None or quadrant is None:
            return dataset
        if quadrant == "Q1":  # X+ Y+
            return dataset[(dataset[x_col] >= cx) & (dataset[y_col] >= cy)]
        elif quadrant == "Q2":  # X- Y+
            return dataset[(dataset[x_col] < cx) & (dataset[y_col] >= cy)]
        elif quadrant == "Q3":  # X- Y-
            return dataset[(dataset[x_col] < cx) & (dataset[y_col] < cy)]
        elif quadrant == "Q4":  # X+ Y-
            return dataset[(dataset[x_col] >= cx) & (dataset[y_col] < cy)]
        return dataset

    if x_col not in dataset.columns or y_col not in dataset.columns:
        return dataset

    # Gate poligonal: lista de vertices [[x, y], ...].
    if gate_type == "polygon":
        vertices = gate_coords.get("vertices") or []
        if len(vertices) >= 3:
            mask = _points_in_polygon(
                dataset[x_col].values, dataset[y_col].values, vertices
            )
            return dataset[mask]
        return dataset

    # Gate retangular (legado / default).
    start_x = gate_coords.get("startX")
    end_x = gate_coords.get("endX")
    start_y = gate_coords.get("startY")
    end_y = gate_coords.get("endY")

    if all(v is not None for v in (start_x, end_x, start_y, end_y)):
        return dataset[
            (dataset[x_col] >= start_x)
            & (dataset[x_col] <= end_x)
            & (dataset[y_col] >= start_y)
            & (dataset[y_col] <= end_y)
        ]
    return dataset
