import json
from rest_framework import serializers
from utils.validators import validate_zip_file
from .models import ExperimentModel, FileDataModel, GateModel


class GateModelSerializer(serializers.ModelSerializer):
    file_data = serializers.PrimaryKeyRelatedField(
        queryset=FileDataModel.objects.all(),
        allow_null=True,
    )
    parent = serializers.PrimaryKeyRelatedField(queryset=GateModel.objects.all(), allow_null=True, required=False)
    class Meta: 
        model = GateModel
        fields = '__all__'
        read_only_fields = ['id', 'created_at']
    def get_children(self, obj):
        # Serializa os filhos do gate
        children = obj.children.all()
        return GateModelSerializer(children, many=True).data


class ListGateSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()
    file_data = serializers.PrimaryKeyRelatedField(
        queryset=FileDataModel.objects.all(),
        allow_null=True,
    )
    parent = serializers.PrimaryKeyRelatedField(queryset=GateModel.objects.all(), allow_null=True, required=False)
   

    class Meta:
        model = GateModel
        fields = ['id', 'created_at', 'data_set', 'parent', 'children', 'file_data', 'name', 'gate_coordinates']

    def get_children(self, obj):
         # Serializa os filhos do gate
        children = obj.children.all()
        return GateModelSerializer(children, many=True).data


class ExperimentSerializer(serializers.ModelSerializer):
    file = serializers.FileField(allow_empty_file=False, write_only=True)
    type = serializers.CharField(allow_null=True, required=True)
    values = serializers.ListField(child=serializers.CharField(), required=False)
    error_info = serializers.JSONField(read_only=True)

    class Meta:
        model = ExperimentModel
        fields = [
            "id",
            "title",
            "file",
            "type",
            "values",
            "active",
            "status",
            "error_info",
        ]
        read_only_fields = ["id", "active", "status", "error_info"]

    def validate(self, data):
        if "file" in data:
            validate_zip_file(data["file"])
        return super().validate(data)


# class GateSerializer(serializers.ModelSerializer):
#     parent = serializers.PrimaryKeyRelatedField(queryset=GateModel.objects.all(), allow_null=True, required=False)
#     class Meta:
#         model = GateModel
#         fields = '__all__'
#         read_only_fields = ['id', 'created_at']
#     def get_children(self, obj):
#         # Serializa os filhos do gate
#         children = obj.children.all()
#         return GateSerializer(children, many=True).data


class ListFileDataSerializer(serializers.ModelSerializer):

    gates = GateModelSerializer(many=True, read_only=True, source='gates_set')

    class Meta:
        model = FileDataModel
        fields = ["id", "file_name", "gates"]
        read_only_fields = ["id"]

    def is_valid(self, raise_exception=False):

        headers = self.initial_data.get("headers")
        data_set = self.initial_data.get("data_set")

        if not self.validate_json_field(headers):
            self.errors["headers"] = ["Formato inválido para o campo headers."]
        if not self.validate_json_field(data_set):
            self.errors["data_set"] = ["Formato inválido para o campo data_set."]

        return super().is_valid(raise_exception)

    def validate_json_field(self, field_data):
        try:
            json.loads(field_data)
            return True
        except ValueError:
            return False


class ParamListDataSerializer(serializers.ModelSerializer):
    gates = GateModelSerializer(many=True, read_only=True)
    
    class Meta:
        model = FileDataModel
        fields = ["id", "file_name", "data_set", "gates"]


class ListExperimentSerializer(serializers.ModelSerializer):
    values = serializers.ListField(child=serializers.CharField())

    class Meta:
        model = ExperimentModel
        fields = "__all__"


class CreateFileModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileDataModel
        fields = ["id", "file_name", "file"]
        read_only_fields = ["id"]
