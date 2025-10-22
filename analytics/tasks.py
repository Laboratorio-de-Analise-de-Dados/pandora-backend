# seu_projeto_django/analytics/tasks.py

from celery import shared_task
import pandas as pd
import numpy as np # Ainda útil para cálculos estatísticos
# import io # Não mais necessário se não for ler o arquivo binário direto

# Importe os modelos necessários para as tasks
from .models import GateModel, AnalysisResult
# Importe o FileDataModel do seu app fcs_parser
# Garanta que o caminho de importação esteja correto para o seu projeto
from fcs_parser.models import FileDataModel 

# --- Funções de Lógica de Negócio (Revisadas) ---

def load_fcs_data_from_file_data_model(file_data_id):
    """
    Carrega os dados FCS do campo `data_set` de um FileDataModel
    e retorna como um DataFrame Pandas.
    """
    try:
        file_data_instance = FileDataModel.objects.get(id=file_data_id)
        
        
        fcs_df = pd.DataFrame(file_data_instance.data_set)
       
        return fcs_df
    except FileDataModel.DoesNotExist:
        print(f"FileDataModel com ID {file_data_id} não encontrado na task.")
        return pd.DataFrame()
    except Exception as e:
        print(f"Erro na task ao carregar dados FCS do FileDataModel para ID {file_data_id}: {e}")
        return pd.DataFrame()




def apply_gate_to_data(fcs_data_df, gate_coordinates, x_param, y_param):
   
    if fcs_data_df.empty:
        return pd.DataFrame()

    filtered_data = fcs_data_df.copy()

    if x_param not in filtered_data.columns or y_param not in filtered_data.columns:
        print(f"Aviso na task: Parâmetros de eixo '{x_param}' ou '{y_param}' não encontrados nos dados.")
        return pd.DataFrame()

    if 'startX' in gate_coordinates and 'endX' in gate_coordinates and \
       'startY' in gate_coordinates and 'endY' in gate_coordinates:
        min_x = gate_coordinates.get('startX')
        max_x = gate_coordinates.get('endX')
        min_y = gate_coordinates.get('startY')
        max_y = gate_coordinates.get('endY')

        filtered_data = filtered_data[
            (filtered_data[x_param] >= min_x) & (filtered_data[x_param] <= max_x) &
            (filtered_data[y_param] >= min_y) & (filtered_data[y_param] <= max_y)
        ]
    else:
        print(f"Aviso na task: Formato de gate_coordinates desconhecido para o gate. Assumindo que não é retangular ou falta dados.")
        return pd.DataFrame() # Retorna vazio se não puder aplicar o gate
    
    return filtered_data


def calculate_cytometry_metrics(gated_data_df, total_events_in_file, parent_gated_data_df=None, all_channel_names=None):
  
    metrics = {
        "summary_metrics": {
            "count": len(gated_data_df),
            "percent_of_total_population": len(gated_data_df) / total_events_in_file if total_events_in_file > 0 else 0,
            "percent_of_parent_population": 0
        },
        "channel_statistics": {}
    }

    if parent_gated_data_df is not None and len(parent_gated_data_df) > 0:
        metrics["summary_metrics"]["percent_of_parent_population"] = len(gated_data_df) / len(parent_gated_data_df)

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
                    "cv": (std_dev_val / mean_val * 100) if mean_val != 0 else 0
                }

    return metrics


@shared_task(bind=True)
def recalculate_gate_analysis_task(self, gate_id):
    print(f"Tarefa {self.request.id}: Iniciando recálculo para gate ID {gate_id}...")
    try:
        gate = GateModel.objects.select_related('dashboard', 'file_data', 'parent').get(id=gate_id)
        
        x_param = gate.dashboard.dashboard_config.get('x_axis_parameter', 'FSC-A')
        y_param = gate.dashboard.dashboard_config.get('y_axis_parameter', 'SSC-A')

        fcs_data_df = load_fcs_data_from_file_data_model(gate.file_data.id)
        if fcs_data_df.empty:
            print(f"Tarefa {self.request.id}: Dados FCS vazios para gate {gate_id}. Abortando.")
            return

        total_events_in_file = len(fcs_data_df)
        all_channel_names = list(fcs_data_df.columns)

        parent_gated_data_df = None
        if gate.parent:
            parent_gate = gate.parent
            parent_x_param = parent_gate.dashboard.dashboard_config.get('x_axis_parameter', 'FSC-A')
            parent_y_param = parent_gate.dashboard.dashboard_config.get('y_axis_parameter', 'SSC-A')
            
            parent_gated_data_df = apply_gate_to_data(
                fcs_data_df, 
                parent_gate.gate_coordinates, 
                parent_x_param,
                parent_y_param
            )

        gated_data_df = apply_gate_to_data(
            fcs_data_df, 
            gate.gate_coordinates, 
            x_param, 
            y_param
        )

        new_analysis_results = calculate_cytometry_metrics(
            gated_data_df, 
            total_events_in_file, 
            parent_gated_data_df,
            all_channel_names
        )

        AnalysisResult.objects.update_or_create(
            gate=gate,
            defaults={'analysis_result': new_analysis_results}
        )
        print(f"Tarefa {self.request.id}: Recálculo concluído para gate '{gate.name}' (ID: {gate_id}).")

        for child_gate in gate.children.all():
            recalculate_gate_analysis_task.delay(child_gate.id)

    except GateModel.DoesNotExist:
        print(f"Tarefa {self.request.id}: GateModel com ID {gate_id} não encontrado.")
    except Exception as e:
        print(f"Tarefa {self.request.id}: Erro inesperado ao recalcular gate {gate_id}: {e}", exc_info=True)