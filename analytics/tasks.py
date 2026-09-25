from __future__ import annotations

import logging

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
            if channel not in gated_data_df.columns:
                continue
            # Só valores finitos entram nas stats: `n` registra quantos
            # eventos do gate têm valor válido no canal (o `count` do
            # resumo continua sendo o total de eventos do gate).
            channel_data = gated_data_df[channel]
            channel_data = channel_data.replace([np.inf, -np.inf], np.nan).dropna()
            if channel_data.empty:
                continue
            mean_val = channel_data.mean()
            median_val = channel_data.median()
            std_dev_val = channel_data.std()
            q16, q84 = np.percentile(channel_data, [16, 84])

            metrics["channel_statistics"][channel] = {
                "n": int(channel_data.size),
                "mean_mfi": mean_val,
                "median_mfi": median_val,
                "std_dev": std_dev_val,
                "cv": (std_dev_val / mean_val * 100) if mean_val != 0 else 0,
                # rCV (robust CV, padrão citometria): (P84 - P16) / (2 *
                # mediana). Continua válido quando a média encosta em zero
                # ou vira negativa (comum pós-compensação), onde cv quebra.
                "rcv": ((q84 - q16) / 2 / median_val * 100) if median_val != 0 else 0,
            }

    return metrics


def recalculate_gate_analysis(gate_id: int):
    """Recalcula métricas de um gate seguindo o padrão FlowJo/Cytobank.

    Percorre toda a cadeia hierárquica de gates (do root até o gate alvo),
    aplicando cada filtro sequencialmente.

    Métricas calculadas:
    - **count**: eventos dentro deste gate.
    - **% of Parent**: count / eventos no gate pai (ou total do arquivo para
      root gates).
    - **% of Total (Grandparent)**: count / total de eventos do arquivo.
    """
    from fcs_parser.services.compensation import (
        applied_compensation,
        apply_compensation,
    )
    from analytics.gate_filter import (
        apply_gate_filter,
        missing_gate_channels,
    )
    from utils.density import normalize_columns

    logger.info("Iniciando recálculo para gate ID %s...", gate_id)
    try:
        gate = GateModel.objects.select_related(
            "dashboard",
            "file_data",
            "parent",
        ).get(id=gate_id)

        fcs_data_df = load_fcs_data_from_file_data_model(gate.file_data.id)
        if fcs_data_df.empty:
            logger.warning("Dados FCS vazios para gate %s. Abortando.", gate_id)
            return

        dataset = normalize_columns(fcs_data_df)
        # BE-22: estatísticas refletem o espaço exibido — com compensação
        # aplicada, os eventos já vêm multiplicados por S⁻¹.
        applied = applied_compensation(gate.file_data.experiment)
        if applied:
            dataset = apply_compensation(dataset, applied.channels, applied.matrix)
        compensated_full = dataset
        total_events_in_file = len(dataset)
        all_channel_names = list(dataset.columns)

        current = gate
        gate_path = [current]
        while current.parent:
            current = current.parent
            gate_path.insert(0, current)

        parent_gated_data_df = None
        blocked_by = None
        missing_channels = []
        for g in gate_path:
            if g.id == gate.id:
                parent_gated_data_df = dataset.copy()
            # Canal ausente torna o gate não-avaliável nesta amostra e corta
            # a linhagem abaixo dele (ADR-0016): sem stat fictícia, o gate
            # fica marcado e os filhos herdam o mesmo marcador na recursão.
            missing = missing_gate_channels(g, dataset.columns)
            if missing:
                blocked_by = g
                missing_channels = missing
                break
            dataset = apply_gate_filter(dataset, g)
            if dataset.empty:
                break

        if blocked_by is not None:
            new_analysis_results = {
                "applicable": False,
                "reason": "missing_channels",
                "missing_channels": missing_channels,
                "blocked_by_gate": {"id": blocked_by.id, "name": blocked_by.name},
            }
        else:
            gated_data_df = dataset

            if parent_gated_data_df is None:
                parent_gated_data_df = compensated_full

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
            recalculate_gate_analysis(child_gate.id)

    except GateModel.DoesNotExist:
        logger.error("GateModel com ID %s não encontrado.", gate_id)
    except Exception as e:
        logger.error(
            "Erro inesperado ao recalcular gate %s: %s",
            gate_id,
            e,
            exc_info=True,
        )
