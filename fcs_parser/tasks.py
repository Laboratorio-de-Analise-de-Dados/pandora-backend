import os
import traceback
from datetime import timedelta
from celery import shared_task
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
import shutil
import pandas as pd
from .models import FileModel, ExperimentModel, FileDataModel, parquet_storage_dir
from .services import decompres_file, process_fcs_file
import zipfile

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
            directory_path = os.path.join(settings.MEDIA_ROOT, "fcs_files", str(experiment.id))
            file_path = file.file.path
            os.makedirs(directory_path, exist_ok=True)
            with zipfile.ZipFile(file_path, "r") as zip_ref:
                zip_ref.extractall(directory_path)
            values = []
            # Pasta persistente onde guardamos os .fcs originais (fonte da verdade).
            fcs_source_dir = os.path.join(
                settings.MEDIA_ROOT, "fcs_source", str(experiment.id)
            )
            os.makedirs(fcs_source_dir, exist_ok=True)
            try:
                for root, dirs, files in os.walk(directory_path): 
                    for file_name in files:
                        if file_name.endswith(".fcs"):
                            complete_path: str = os.path.join(root, file_name)
                            processed_file = process_fcs_file(complete_path)
                            if isinstance(processed_file, str):
                                raise ValueError(processed_file)
                            if len(values) == 0:
                                values = processed_file[2]
                            # Mantem o .fcs original como fonte (permite reprocessar).
                            persistent_fcs = os.path.join(fcs_source_dir, file_name)
                            shutil.copy2(complete_path, persistent_fcs)
                            file_data_model = FileDataModel.objects.create(
                                headers=processed_file[0],
                                data_set=None,
                                experiment=experiment,
                                file_name=file_name,
                                file=file,
                                fcs_path=persistent_fcs,
                            )
                            # Grava o parseado em Parquet (cache morno) em vez de JSON.
                            file_data_model.save_dataframe(
                                pd.DataFrame(processed_file[1])
                            )
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


@shared_task
def cleanup_cold_parquet_task(max_idle_days: int = 7):
    """Limpa Parquet regeneravel: orfaos (sem linha no banco) e frios (sem acesso
    ha mais de ``max_idle_days`` dias). Como o .fcs original e a fonte da verdade,
    o Parquet some sem perda: o proximo ``get_dataframe`` o reconstroi.
    """
    removed = 0
    parquet_dir = parquet_storage_dir()

    # 1) Orfaos: arquivos .parquet no disco que nenhuma linha referencia.
    if os.path.isdir(parquet_dir):
        referenced = set(
            FileDataModel.objects.exclude(parquet_path__isnull=True)
            .exclude(parquet_path="")
            .values_list("parquet_path", flat=True)
        )
        for name in os.listdir(parquet_dir):
            full = os.path.join(parquet_dir, name)
            if os.path.isfile(full) and full not in referenced:
                try:
                    os.remove(full)
                    removed += 1
                except OSError:
                    pass

    # 2) Frios: linhas cujo Parquet nao e acessado ha muito tempo e que tem .fcs
    #    para reconstruir depois.
    cutoff = timezone.now() - timedelta(days=max_idle_days)
    cold = FileDataModel.objects.exclude(parquet_path__isnull=True).exclude(
        parquet_path=""
    ).filter(last_accessed__lt=cutoff, fcs_path__isnull=False)
    for file_data in cold:
        path = file_data.parquet_path
        if path and os.path.exists(path):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                continue
        FileDataModel.objects.filter(pk=file_data.pk).update(parquet_path=None)

    print(f"INFO: cleanup_cold_parquet_task removeu {removed} arquivo(s) Parquet.")
    return removed


@shared_task
def recompute_file_data_task(file_data_id: int):
    """Plano D: regenera o cache (Parquet) a partir do .fcs original (fonte) e
    invalida a density no Redis, alem de recalcular os gates do arquivo.

    Fecha o ciclo "receita reconstroi tudo": .fcs (L0) + gates (L1) -> L2/L3.
    """
    from analytics.models import GateModel
    from analytics.tasks import recalculate_gate_analysis_task
    from utils.density import invalidate_density

    file_data = get_object_or_404(FileDataModel, id=file_data_id)

    if not (file_data.fcs_path and os.path.exists(file_data.fcs_path)):
        print(
            f"WARN: recompute_file_data_task: .fcs ausente para FileData {file_data_id}."
        )
        return {"status": "skipped", "reason": "fcs_not_found"}

    processed = process_fcs_file(file_data.fcs_path)
    if isinstance(processed, str):
        raise ValueError(processed)

    file_data.save_dataframe(pd.DataFrame(processed[1]))

    # Invalida heatmaps/stats no Redis (bump de versao) e refaz analise dos gates.
    invalidate_density(file_data_id)
    for gate_id in GateModel.objects.filter(file_data_id=file_data_id).values_list(
        "id", flat=True
    ):
        recalculate_gate_analysis_task.delay(gate_id)

    print(f"INFO: recompute_file_data_task regenerou FileData {file_data_id}.")
    return {"status": "ok", "file_data_id": file_data_id}