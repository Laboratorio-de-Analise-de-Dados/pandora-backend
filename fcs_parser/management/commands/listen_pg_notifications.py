# your_app/management/commands/listen_notifications.py
import json
import os
import select
import traceback
from django.core.management.base import BaseCommand
from fcsparser import parse
import psycopg2
from django.conf import settings

from fcs_parser.models import FileDataModel, FileModel
from fcs_parser.services import process_experiment_file
from fcs_parser.services.decompressor import decompres_file
from fcs_parser.services.header_parser import serialize_value
from fcs_parser.services.process_fcs import process_fcs_file

class Command(BaseCommand):
    help = 'Listen for PostgreSQL notifications'

    def handle(self, *args, **kwargs):
      conn = psycopg2.connect(
        dbname=settings.DATABASES['default']['NAME'],
        user=settings.DATABASES['default']['USER'],
        password=settings.DATABASES['default']['PASSWORD'],
        host=settings.DATABASES['default']['HOST'],
        port=settings.DATABASES['default']['PORT'],
      )
      conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
      cur = conn.cursor()
      cur.execute('LISTEN new_file;')
      self.stdout.write('Waiting for notifications on channel "new_file"')

      while True:
        if select.select([conn], [], [], 5) == ([], [], []):
          continue
        conn.poll()
        while conn.notifies:
          notify = conn.notifies.pop(0)
          file_id = notify.payload
          self.stdout.write(f'Notification received: {file_id}')
          self.process_file(file_id)

    def process_file(self, file_id):
      try:
        file = FileModel.objects.get(id=file_id)
        experiment = file.experiment
        if experiment.status == 'new':
          experiment.status = 'processing'
          experiment.save()
          self.process_experiment_file(file)
          experiment.status = 'done'
          experiment.save()
      except Exception as e:
        self.stdout.write(f'{e}')

    def process_experiment_file(self, file: FileModel):
      experiment_title = file.file_name
      self.stdout.write(f'Experiment name received: {file.file_name}')
      directory_path = os.path.join(settings.BASE_DIR, 'assets', 'fcs_files', experiment_title)
      file_path = os.path.join(settings.BASE_DIR,'storage', file.file_name)
      os.makedirs(directory_path, exist_ok=True)
      decompres_file(file_path, directory_path)
      experiment = file.experiment
      values = []
      
      try:
        for file_name in os.listdir(directory_path):
          if file_name.endswith(".fcs"):
            complete_path: str = os.path.join(directory_path, file_name)
            processed_file = self.process_fcs_file(complete_path)
            if len(values) == 0:
              values = processed_file[2]
            self.stdout.write(f'creating')
            file_data_model = FileDataModel.objects.create(headers=processed_file[0], data_set=processed_file[1], experiment=experiment, file_name=file_name, file=file)
            file_data_model.save()
            self.stdout.write(f'created')
            os.remove(complete_path)
        experiment.values = values
        experiment.status = 'done'
        experiment.save()

      except Exception as e:
        error_info = {
            'error_message': str(e),
            'details': traceback.format_exc()
        }
        experiment.status = 'error'
        experiment.error_info = error_info
        experiment.save()

    def process_fcs_file(self, fcs_file_path: str):
      try:
          with open(fcs_file_path, 'rb') as f:
              headers, data_set = parse(fcs_file_path)
              data_set['id'] = range(1, len(data_set) + 1)
              self.stdout.write(f'fcs_file_path received: {fcs_file_path}')

              json_dataset = data_set.to_json(orient='records')
              serialized_header = {key.replace("_", "").replace(" ", "_").lower(): serialize_value(value) for key, value in headers.items()}

              json_header = json.dumps(serialized_header, indent=2)
              
              values = data_set.columns.tolist()
              return [json_header, json.loads(json_dataset), values]

      except Exception as e:
          return f'Error processing FCS file: {str(e)}'