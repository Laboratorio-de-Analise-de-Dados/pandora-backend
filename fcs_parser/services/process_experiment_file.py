import os
import traceback
from django.conf import settings

from fcs_parser.models import ExperimentModel, FileDataModel, FileModel
from fcs_parser.services import decompres_file, process_fcs_file


def process_experiment_file(file: FileModel):

    experiment_title = file.file_name
    directory_path = os.path.join(settings.MEDIA_ROOT, "fcs_files", file.file_name)
    file_path = os.path.join(settings.MEDIA_ROOT, file.file_name)
    os.makedirs(directory_path, exist_ok=True)
    decompres_file(file_path, directory_path)
    experiment = file.experiment
    values = []

    try:
        for file_name in os.listdir(directory_path):
            if file_name.endswith(".fcs"):
                complete_path: str = os.path.join(directory_path, file_name)
                processed_file = process_fcs_file(complete_path)
                experiment_id = experiment.id
                if len(values) == 0:
                    values = processed_file[2]
                FileDataModel.objects.get_or_create(
                    headers=processed_file[0],
                    data_set=processed_file[1],
                    experiment_id=experiment_id,
                    file_name=file_name,
                )
                os.remove(complete_path)
        experiment.values = values
        experiment.status = "done"
        experiment.save()

    except Exception as e:
        error = {"error_message": str(e), "details": traceback.format_exc()}
        error_handler(e, experiment, error)


def error_handler(e: Exception, experiment: ExperimentModel, error_info: dict):
    experiment.status = "error"
    experiment.error_info = error_info
    experiment.save()
