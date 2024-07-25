import json
from rest_framework import serializers
from utils.validators import validate_zip_file
from .models import ExperimentModel, FileDataModel, GateModel


class GateModelSerializer(serializers.ModelSerializer):
    file_data = serializers.PrimaryKeyRelatedField(
        queryset=FileDataModel.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = GateModel
        fields = "__all__"


class ListGateSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()

    class Meta:
        model = GateModel
        fields = "__all__"

    def get_children(self, obj):
        return ListGateSerializer(obj.children.all(), many=True).data


class ExperimentSerializer(serializers.ModelSerializer):
    file = serializers.FileField(allow_empty_file=False, write_only=True)
    type = serializers.CharField(allow_null=True, required=False)
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


class ListFileDataSerializer(serializers.ModelSerializer):

    class Meta:
        model = FileDataModel
        fields = ["id", "file_name"]
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

    class Meta:
        model = FileDataModel
        fields = ["id", "file_name", "data_set"]


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
