from django.db import models

class ExperimentModel(models.Model):
    """Model for FCS file"""
    id = models.BigAutoField(primary_key=True)
    title = models.CharField(max_length=50, unique=True)

    class Meta:
      db_table='experiment'

class FileDataModel(models.Model):
  """Model for Data on each file"""
  id = models.BigAutoField(primary_key=True)
  experiment = models.ForeignKey(ExperimentModel, on_delete=models.CASCADE)
  headers = models.JSONField()
  data_set = models.JSONField()
  
  class Meta:
      db_table='file_data'

  def is_valid(self, raise_exception=False):
    headers = self.initial_data.get('headers')
    data_set = self.initial_data.get('data_set')

    if not self.validate_json_field(headers):
        self.errors['headers'] = ['Invalid field headers.']
    if not self.validate_json_field(data_set):
        self.errors['data_set'] = ['Invalid field data_set.']

    return super().is_valid(raise_exception)


