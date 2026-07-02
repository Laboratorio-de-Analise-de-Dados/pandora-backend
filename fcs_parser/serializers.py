from rest_framework import serializers
from analytics.serializers import ListGateSerializer
from utils.validators import validate_zip_file
from .models import ExperimentModel, FileDataModel


class ExperimentSerializer(serializers.ModelSerializer):
    file = serializers.FileField(allow_empty_file=False, write_only=True)
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

    gates = ListGateSerializer(many=True, read_only=True)

    class Meta:
        model = FileDataModel
        fields = ["id", "file_name", "gates"]
        read_only_fields = ["id"]


class ParamListDataSerializer(serializers.ModelSerializer):
    gates = ListGateSerializer(many=True, read_only=True)

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
