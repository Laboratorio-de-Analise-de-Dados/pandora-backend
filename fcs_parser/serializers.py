import json
from rest_framework import serializers
from utils.validators import validate_file_size, validate_zip_file
from .models import ExperimentModel, FileDataModel

class ExperimentSerializer(serializers.ModelSerializer):
    
  file = serializers.FileField(allow_empty_file=False, write_only=True)

  
  class Meta:
    model = ExperimentModel
    fields = ['id', 'title', 'file']
    read_only_fields = ['id'] 

  def is_valid(self, *, raise_exception=False):
    validate_zip_file(self.initial_data.get('file'))
    return super().is_valid(raise_exception=raise_exception)
  
class ListFileDataSerializer(serializers.ModelSerializer):

  class Meta:
    model = FileDataModel
    fields = ['id', 'file_name']
    read_only_fields = ['id'] 

  def is_valid(self, raise_exception=False):
   
    headers = self.initial_data.get('headers')
    data_set = self.initial_data.get('data_set')

    if not self.validate_json_field(headers):
        self.errors['headers'] = ['Formato inválido para o campo headers.']
    if not self.validate_json_field(data_set):
        self.errors['data_set'] = ['Formato inválido para o campo data_set.']

    return super().is_valid(raise_exception)
  
  def validate_json_field(self, field_data):
    try:
        json.loads(field_data)
        return True
    except ValueError:
        return False
    
class ParamListDataSerializer(serializers.ModelSerializer):

  class Meta:
    model = FileDataModel
    fields = ['id', 'data_set', 'file_name']
    

class ListExperimentSerializer(serializers.ModelSerializer):
   class Meta:
      model = ExperimentModel
      fields = "__all__"