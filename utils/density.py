import numpy as np
import pandas as pd
from django.conf import settings
from django.core.cache import cache


def _version(file_data_id) -> int:
    """Current cache version for a file's density entries. Bumped to invalidate."""
    try:
        v = cache.get(f"density:ver:{file_data_id}")
        return v if v is not None else 1
    except Exception:
        return 1


def invalidate_density(file_data_id):
    """Invalidate every density cache entry derived from a file (its file + gate heatmaps)."""
    try:
        cache.incr(f"density:ver:{file_data_id}")
    except Exception:
        try:
            cache.set(f"density:ver:{file_data_id}", 2, None)
        except Exception:
            pass


def density_cache_key(
    scope, file_data_id, obj_id, x, y, mode, bins, sample, x_scale, y_scale,
    cofactor, cutoff=0
) -> str:
    return (
        f"density:{scope}:{obj_id}:v{_version(file_data_id)}:{x}:{y}:{mode}:{bins}"
        f":{sample}:{x_scale}:{y_scale}:{cofactor}:{cutoff}"
    )


def get_cached_density(key):
    """Return cached payload and renew its TTL (sliding). Degrades to None on any cache error."""
    try:
        value = cache.get(key)
        if value is not None:
            cache.touch(key, settings.DENSITY_CACHE_TTL)
        return value
    except Exception:
        return None


def set_cached_density(key, value):
    """Store payload with the configured TTL. Silently ignores cache errors."""
    try:
        cache.set(key, value, settings.DENSITY_CACHE_TTL)
    except Exception:
        pass


# Cofator padrao do arcsinh para fluorescencia em citometria de fluxo.
DEFAULT_COFACTOR = 150.0


def parse_range(query_params, min_key: str, max_key: str):
    """Return (min, max) tuple from query params, or None if neither is set."""
    raw_min = query_params.get(min_key)
    raw_max = query_params.get(max_key)
    if raw_min is None and raw_max is None:
        return None
    lo = float(raw_min) if raw_min is not None else -float("inf")
    hi = float(raw_max) if raw_max is not None else float("inf")
    return (lo, hi)


def normalize_column_name(name: str) -> str:
    return name.lower().replace(" ", "").replace("-", "_")


def default_scale(param: str) -> str:
    """Heuristica: FSC/SSC/Time sao lineares; demais canais usam biex (arcsinh)."""
    p = normalize_column_name(param)
    if p.startswith("fsc") or p.startswith("ssc") or p == "time":
        return "linear"
    return "biex"


def apply_scale(values, scale: str, cofactor: float = DEFAULT_COFACTOR):
    """Transforma valores para exibicao. 'biex' = arcsinh(x/cofator) (lida com
    negativos e e quase-linear perto de zero); 'linear' = identidade."""
    if scale == "biex":
        return np.arcsinh(np.asarray(values, dtype=float) / cofactor)
    return values


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = df.columns.str.replace(" ", "")
    df.columns = df.columns.str.replace("-", "_")
    df.columns = df.columns.str.lower()
    return df


def compute_density(
    df: pd.DataFrame,
    x_param: str,
    y_param: str,
    bins: int = 200,
    x_scale: str = "linear",
    y_scale: str = "linear",
    cofactor: float = DEFAULT_COFACTOR,
    cutoff: int = 0,
    x_range: tuple | None = None,
    y_range: tuple | None = None,
):
    """Return a 2-D histogram dict ready to be sent as JSON.

    Os eixos sao transformados (biex/linear) ANTES do binning, para que as bins
    fiquem uniformes no espaco exibido. As bordas retornadas estao no espaco
    transformado; o front mapeia os ticks de volta para unidades reais.

    Bins com contagem <= cutoff viram None (null no JSON) para que o front os
    renderize transparentes em vez de pintados na cor mais fria do colormap.
    Com cutoff=0 (default) apenas as bins vazias somem; valores maiores cortam
    ruido/outliers de baixa densidade.
    """
    x_col = normalize_column_name(x_param)
    y_col = normalize_column_name(y_param)

    if x_col not in df.columns or y_col not in df.columns:
        return None

    x = pd.to_numeric(df[x_col], errors="coerce").dropna()
    y = pd.to_numeric(df[y_col], errors="coerce").dropna()
    idx = x.index.intersection(y.index)
    x, y = x.loc[idx], y.loc[idx]

    if len(x) == 0:
        return None

    xv = apply_scale(x.values, x_scale, cofactor)
    yv = apply_scale(y.values, y_scale, cofactor)

    hist_range = None
    if x_range or y_range:
        xr = (
            [apply_scale(np.array([x_range[0]]), x_scale, cofactor)[0],
             apply_scale(np.array([x_range[1]]), x_scale, cofactor)[0]]
            if x_range
            else [float(np.min(xv)), float(np.max(xv))]
        )
        yr = (
            [apply_scale(np.array([y_range[0]]), y_scale, cofactor)[0],
             apply_scale(np.array([y_range[1]]), y_scale, cofactor)[0]]
            if y_range
            else [float(np.min(yv)), float(np.max(yv))]
        )
        hist_range = [xr, yr]

    histogram, x_edges, y_edges = np.histogram2d(
        xv, yv, bins=bins, range=hist_range,
    )

    counts = histogram.T.astype(int)
    # Bins <= cutoff -> None (null) para o front renderizar transparente.
    threshold = max(int(cutoff), 0)
    masked = np.where(counts > threshold, counts, None)
    histogram_json = [
        [None if v is None else int(v) for v in row]
        for row in masked.tolist()
    ]

    return {
        "histogram": histogram_json,
        "x_edges": np.round(x_edges, 4).tolist(),
        "y_edges": np.round(y_edges, 4).tolist(),
        "x_scale": x_scale,
        "y_scale": y_scale,
        "cofactor": cofactor,
        "cutoff": threshold,
    }


def compute_histogram(
    df: pd.DataFrame,
    x_param: str,
    bins: int = 256,
    x_scale: str = "linear",
    cofactor: float = DEFAULT_COFACTOR,
    x_range: tuple | None = None,
):
    """Return a 1-D histogram dict ready to be sent as JSON.

    Histogram unidimensional (distribuicao de um unico parametro). Os bins
    ficam uniformes no espaco transformado (biex/linear).
    """
    x_col = normalize_column_name(x_param)

    if x_col not in df.columns:
        return None

    x = pd.to_numeric(df[x_col], errors="coerce").dropna()

    if x_range:
        x = x[(x >= x_range[0]) & (x <= x_range[1])]

    if len(x) == 0:
        return None

    xv = apply_scale(x.values, x_scale, cofactor)

    hist_range = None
    if x_range:
        hist_range = (
            float(apply_scale(np.array([x_range[0]]), x_scale, cofactor)[0]),
            float(apply_scale(np.array([x_range[1]]), x_scale, cofactor)[0]),
        )

    counts, edges = np.histogram(xv, bins=bins, range=hist_range)

    return {
        "counts": counts.tolist(),
        "edges": np.round(edges, 4).tolist(),
        "x_scale": x_scale,
        "cofactor": cofactor,
    }


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


def apply_gate_filter(dataset: pd.DataFrame, gate) -> pd.DataFrame:
    """Apply a single gate's filter (rectangle or polygon) to a DataFrame.

    Colunas ja normalizadas; coordenadas do gate sempre em espaco cru (linear),
    independente da escala usada para exibir o grafico.
    """
    gate_coords = gate.gate_coordinates

    x_label = "fsc_a"
    y_label = "ssc_a"

    if hasattr(gate, "dashboard") and gate.dashboard and gate.dashboard.dashboard_config:
        config = gate.dashboard.dashboard_config
        x_label = normalize_column_name(config.get("x_axis_label", x_label))
        y_label = normalize_column_name(config.get("y_axis_label", y_label))

    if x_label not in dataset.columns or y_label not in dataset.columns:
        return dataset

    # Gate poligonal: lista de vertices [[x, y], ...].
    if gate_coords.get("type") == "polygon":
        vertices = gate_coords.get("vertices") or []
        if len(vertices) >= 3:
            mask = _points_in_polygon(
                dataset[x_label].values, dataset[y_label].values, vertices
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
            (dataset[x_label] >= start_x) & (dataset[x_label] <= end_x)
            & (dataset[y_label] >= start_y) & (dataset[y_label] <= end_y)
        ]
    return dataset


def subsample_scatter(
    df: pd.DataFrame,
    x_param: str,
    y_param: str,
    sample: int = 5000,
    x_scale: str = "linear",
    y_scale: str = "linear",
    cofactor: float = DEFAULT_COFACTOR,
    x_range: tuple | None = None,
    y_range: tuple | None = None,
):
    """Return a subsampled scatter dict ready to be sent as JSON (espaco exibido)."""
    x_col = normalize_column_name(x_param)
    y_col = normalize_column_name(y_param)

    if x_col not in df.columns or y_col not in df.columns:
        return None

    subset = df[[x_col, y_col]].dropna()

    if x_range:
        subset = subset[(subset[x_col] >= x_range[0]) & (subset[x_col] <= x_range[1])]
    if y_range:
        subset = subset[(subset[y_col] >= y_range[0]) & (subset[y_col] <= y_range[1])]

    if len(subset) > sample:
        subset = subset.sample(n=sample, random_state=42)

    xv = apply_scale(subset[x_col].values, x_scale, cofactor)
    yv = apply_scale(subset[y_col].values, y_scale, cofactor)

    return {
        "x": np.round(xv, 4).tolist() if x_scale == "biex" else subset[x_col].tolist(),
        "y": np.round(yv, 4).tolist() if y_scale == "biex" else subset[y_col].tolist(),
        "sampled_events": len(subset),
        "x_scale": x_scale,
        "y_scale": y_scale,
        "cofactor": cofactor,
    }
