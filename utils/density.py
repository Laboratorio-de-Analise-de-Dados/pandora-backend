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


def density_cache_key(scope, file_data_id, obj_id, x, y, mode, bins, sample) -> str:
    return f"density:{scope}:{obj_id}:v{_version(file_data_id)}:{x}:{y}:{mode}:{bins}:{sample}"


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


def normalize_column_name(name: str) -> str:
    return name.lower().replace(" ", "").replace("-", "_")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = df.columns.str.replace(" ", "")
    df.columns = df.columns.str.replace("-", "_")
    df.columns = df.columns.str.lower()
    return df


def compute_density(df: pd.DataFrame, x_param: str, y_param: str, bins: int = 200):
    """Return a 2-D histogram dict ready to be sent as JSON."""
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

    histogram, x_edges, y_edges = np.histogram2d(
        x.values, y.values, bins=bins,
    )

    return {
        "histogram": histogram.T.astype(int).tolist(),
        "x_edges": np.round(x_edges, 4).tolist(),
        "y_edges": np.round(y_edges, 4).tolist(),
    }


def apply_gate_filter(dataset: pd.DataFrame, gate) -> pd.DataFrame:
    """Apply a single gate's rectangular filter to a DataFrame (columns already normalized)."""
    gate_coords = gate.gate_coordinates

    x_label = "fsc_a"
    y_label = "ssc_a"

    if hasattr(gate, "dashboard") and gate.dashboard and gate.dashboard.dashboard_config:
        config = gate.dashboard.dashboard_config
        x_label = normalize_column_name(config.get("x_axis_label", x_label))
        y_label = normalize_column_name(config.get("y_axis_label", y_label))

    if x_label not in dataset.columns or y_label not in dataset.columns:
        return dataset

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


def subsample_scatter(df: pd.DataFrame, x_param: str, y_param: str, sample: int = 5000):
    """Return a subsampled scatter dict ready to be sent as JSON."""
    x_col = normalize_column_name(x_param)
    y_col = normalize_column_name(y_param)

    if x_col not in df.columns or y_col not in df.columns:
        return None

    subset = df[[x_col, y_col]].dropna()

    if len(subset) > sample:
        subset = subset.sample(n=sample, random_state=42)

    return {
        "x": subset[x_col].tolist(),
        "y": subset[y_col].tolist(),
        "sampled_events": len(subset),
    }
