from typing import Any
from django.db import models
from django.contrib.postgres.fields import ArrayField

class ExperimentModel(models.Model):
    """Model for FCS file"""
    id = models.BigAutoField(primary_key=True)
    title = models.CharField(max_length=50, unique=True)
    type = models.CharField(max_length=100, null=True)
    values = ArrayField(models.TextField(), blank=True, default=list)
    active = models.BooleanField(default=True)
    
    class Meta:
      db_table='experiment'
 
    def delete(self, using: Any = ..., keep_parents: bool = ...) -> tuple[int, dict[str, int]]:
       self.active = False
       self.save()
       return 

class FileDataModel(models.Model):
  """Model for Data on each file"""
  id = models.BigAutoField(primary_key=True)
  file_name = models.CharField(max_length=256, null=True)
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


