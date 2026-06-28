from __future__ import annotations

import logging

from celery import shared_task
import pandas as pd
import numpy as np

from .models import GateModel, AnalysisResult
from fcs_parser.models import FileDataModel

logger = logging.getLogger(__name__)


def load_fcs_data_from_file_data_model(file_data_id: int) -> pd.DataFrame:
    """Load FCS data from a FileDataModel and return as a DataFrame."""
    try:
        file_data_instance = FileDataModel.objects.get(id=file_data_id)
        return file_data_instance.get_dataframe()
    except FileDataModel.DoesNotExist:
        logger.error("FileDataModel com ID %s não encontrado.", file_data_id)
        return pd.DataFrame()
    except Exception as e:
        logger.error(
            "Erro ao carregar dados FCS para FileDataModel %s: %s",
            file_data_id,
            e,
        )
        return pd.DataFrame()


def apply_gate_to_data(fcs_data_df, gate_coordinates, x_param, y_param):

    if fcs_data_df.empty:
        return pd.DataFrame()

    filtered_data = fcs_data_df.copy()

    if x_param not in filtered_data.columns or y_param not in filtered_data.columns:
        logger.warning(
            "Parâmetros '%s' ou '%s' não encontrados nos dados.", x_param, y_param
        )
        return pd.DataFrame()

    if gate_coordinates.get("type") == "polygon":
        from utils.density import _points_in_polygon

        vertices = gate_coordinates.get("vertices") or []
        if len(vertices) < 3:
            logger.warning("Polígono com menos de 3 vértices. Ignorando.")
            return pd.DataFrame()
        mask = _points_in_polygon(
            filtered_data[x_param].values, filtered_data[y_param].values, vertices
        )
        return filtered_data[mask]

    if (
        "startX" in gate_coordinates
        and "endX" in gate_coordinates
        and "startY" in gate_coordinates
        and "endY" in gate_coordinates
    ):
        min_x = gate_coordinates.get("startX")
        max_x = gate_coordinates.get("endX")
        min_y = gate_coordinates.get("startY")
        max_y = gate_coordinates.get("endY")

        filtered_data = filtered_data[
            (filtered_data[x_param] >= min_x)
            & (filtered_data[x_param] <= max_x)
            & (filtered_data[y_param] >= min_y)
            & (filtered_data[y_param] <= max_y)
        ]
    else:
        logger.warning("Formato de gate_coordinates desconhecido.")
        return pd.DataFrame()

    return filtered_data


def calculate_cytometry_metrics(
    gated_data_df,
    total_events_in_file,
    parent_gated_data_df=None,
    all_channel_names=None,
):

    metrics = {
        "summary_metrics": {
            "count": len(gated_data_df),
            "percent_of_total_population": (
                len(gated_data_df) / total_events_in_file
                if total_events_in_file > 0
                else 0
            ),
            "percent_of_parent_population": 0,
        },
        "channel_statistics": {},
    }

    if parent_gated_data_df is not None and len(parent_gated_data_df) > 0:
        metrics["summary_metrics"]["percent_of_parent_population"] = len(
            gated_data_df
        ) / len(parent_gated_data_df)

    if all_channel_names:
        for channel in all_channel_names:
            if channel in gated_data_df.columns and not gated_data_df[channel].empty:
                channel_data = gated_data_df[channel]
                mean_val = channel_data.mean()
                median_val = channel_data.median()
                std_dev_val = channel_data.std()

                metrics["channel_statistics"][channel] = {
                    "mean_mfi": mean_val,
                    "median_mfi": median_val,
                    "std_dev": std_dev_val,
                    "cv": (std_dev_val / mean_val * 100) if mean_val != 0 else 0,
                }

    return metrics


@shared_task(bind=True)
def recalculate_gate_analysis_task(self, gate_id):
    """Recalcula métricas de um gate seguindo o padrão FlowJo/Cytobank.

    Percorre toda a cadeia hierárquica de gates (do root até o gate alvo),
    aplicando cada filtro sequencialmente — assim como ``GateDensityView`` e
    ``GetGateDataView`` já fazem.

    Métricas calculadas:
    - **count**: eventos dentro deste gate.
    - **% of Parent**: count / eventos no gate pai (ou total do arquivo para
      root gates).
    - **% of Total (Grandparent)**: count / total de eventos do arquivo.
    """
    from utils.density import apply_gate_filter, normalize_columns

    logger.info("Iniciando recálculo para gate ID %s...", gate_id)
    try:
        gate = GateModel.objects.select_related(
            "dashboard",
            "file_data",
            "parent",
        ).get(id=gate_id)

        # --- 1. Dados brutos do arquivo ----------------------------------
        fcs_data_df = load_fcs_data_from_file_data_model(gate.file_data.id)
        if fcs_data_df.empty:
            logger.warning("Dados FCS vazios para gate %s. Abortando.", gate_id)
            return

        dataset = normalize_columns(fcs_data_df)
        total_events_in_file = len(dataset)
        all_channel_names = list(dataset.columns)

        # --- 2. Cadeia hierárquica root → … → gate -----------------------
        current = gate
        gate_path = [current]
        while current.parent:
            current = current.parent
            gate_path.insert(0, current)

        # --- 3. Aplica gates sequencialmente (como FlowJo/Cytobank) ------
        parent_gated_data_df = None
        for g in gate_path:
            if g.id == gate.id:
                # Guarda dados do pai (tudo antes deste gate)
                parent_gated_data_df = dataset.copy()
            dataset = apply_gate_filter(dataset, g)
            if dataset.empty:
                break

        gated_data_df = dataset

        # Para root gates sem parent, % of Parent = % of Total
        if parent_gated_data_df is None:
            parent_gated_data_df = normalize_columns(fcs_data_df)

        # --- 4. Calcula métricas -----------------------------------------
        new_analysis_results = calculate_cytometry_metrics(
            gated_data_df,
            total_events_in_file,
            parent_gated_data_df,
            all_channel_names,
        )

        AnalysisResult.objects.update_or_create(
            gate=gate,
            defaults={"analysis_result": new_analysis_results},
        )
        logger.info("Recálculo concluído para gate '%s' (ID: %s).", gate.name, gate_id)

        for child_gate in gate.children.all():
            recalculate_gate_analysis_task.delay(child_gate.id)

    except GateModel.DoesNotExist:
        logger.error("GateModel com ID %s não encontrado.", gate_id)
    except Exception as e:
        logger.error(
            "Erro inesperado ao recalcular gate %s: %s",
            gate_id,
            e,
            exc_info=True,
        )
