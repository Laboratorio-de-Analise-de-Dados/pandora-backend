import os
import traceback
from celery import shared_task
from django.conf import settings
from django.shortcuts import get_object_or_404
import shutil
import pandas as pd 
from .models import FileModel, ExperimentModel, FileDataModel 
from .services import decompres_file, process_fcs_file 

@shared_task
def process_experiment_files_task(file_id: int):
    """
    Processa arquivos FCS de forma assíncrona para um dado FileModel.
    Descomprime o arquivo, lê os FCS individuais, cria FileDataModel e atualiza o Experiment.
    """
    try:
        
        file = get_object_or_404(FileModel, id=file_id)
        experiment = file.experiment
        experiment = file.experiment
        if experiment.status != 'processing':
            experiment.status = 'processing'
            experiment.save()
            print(file.file_name)
            experiment_title = file.file_name
            directory_path = os.path.join(settings.BASE_DIR, 'assets', 'fcs_files', experiment_title)
            file_path = os.path.join(settings.BASE_DIR,'storage', file.file_name)
            os.makedirs(directory_path, exist_ok=True)
            decompres_file(file_path, directory_path)
            values = []
            file_data_models = []
            print(directory_path)
            try:
                for root, dirs, files in os.walk(directory_path): 
                    print(root)
                    for file_name in files:
                        print(file_name)
                        if file_name.endswith(".fcs"):
                            complete_path: str = os.path.join(root, file_name)
                            processed_file = process_fcs_file(complete_path)
                            if len(values) == 0:
                                values = processed_file[2]
                            file_data_model = FileDataModel(
                                headers=processed_file[0],
                                data_set=processed_file[1],
                                experiment=experiment,
                                file_name=file_name,
                                file=file
                            )
                            file_data_models.append(file_data_model)
                            if len(file_data_models) == 10:
                                FileDataModel.objects.bulk_create(file_data_models)
                                file_data_models = []
                if len(file_data_models) > 0:
                    FileDataModel.objects.bulk_create(file_data_models)
                experiment.values = values
                experiment.status = 'done'
                experiment.save()
                print(f"SUCCESS: Processamento do Experimento {experiment.id} ('{experiment.title}') concluído.")
                if os.path.exists(directory_path):
                    shutil.rmtree(directory_path)
                    print(f"INFO: Diretório temporário '{directory_path}' removido.")
                
                if os.path.exists(file_path):
                    os.remove(file_path)
                    print(f"INFO: Arquivo compactado original '{file_path}' removido.")
            except Exception as e:
                error_info = {
                    'error_message': str(e),
                    'details': traceback.format_exc()
                }
                experiment.status = 'error'
                experiment.error_info = error_info
                experiment.save()
        
    except Exception as e:

        experiment = ExperimentModel.objects.get(id=experiment.id) 
      

        error_info = {
            'error_message': str(e),
            'details': traceback.format_exc() 
        }
        experiment.status = 'error'
        experiment.error_info = error_info
        experiment.save(update_fields=['status', 'error_info'])
        print(f"ERROR: Erro durante o processamento do Experimento {experiment.id}: {e}\n{traceback.format_exc()}")