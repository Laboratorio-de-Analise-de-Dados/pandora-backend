from rest_framework import serializers
from accounts.serializers import OrganizationListSerializer
from analytics.serializers import ListGateSerializer
from utils.validators import validate_zip_file
from .models import ExperimentModel, FileDataModel, SubsampleModel


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


class SubsampleSerializer(serializers.ModelSerializer):
    files_count = serializers.SerializerMethodField()

    class Meta:
        model = SubsampleModel
        fields = [
            "id",
            "name",
            "source_path",
            "active",
            "created_at",
            "files_count",
        ]
        read_only_fields = ["id", "source_path", "active", "created_at"]

    def get_files_count(self, obj) -> int:
        return obj.files.filter(active=True).count()

    def validate_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Nome do subsample é obrigatório.")
        return name


class ListFileDataSerializer(serializers.ModelSerializer):

    gates = ListGateSerializer(many=True, read_only=True)

    class Meta:
        model = FileDataModel
        fields = [
            "id",
            "file_name",
            "source_path",
            "subsample",
            "gates",
            "active",
            "deactivated_at",
        ]
        read_only_fields = ["id", "source_path", "active", "deactivated_at"]


class ParamListDataSerializer(serializers.ModelSerializer):
    gates = ListGateSerializer(many=True, read_only=True)

    class Meta:
        model = FileDataModel
        fields = ["id", "file_name", "data_set", "gates"]


class UpdateExperimentSerializer(serializers.ModelSerializer):
    """Escrita de experimento: só os campos que o usuário pode corrigir."""

    values = serializers.ListField(child=serializers.CharField(), required=False)

    class Meta:
        model = ExperimentModel
        fields = ["title", "type", "values"]

    def validate_title(self, value):
        title = value.strip().replace(" ", "_")
        if not title:
            raise serializers.ValidationError("Título é obrigatório.")
        return title

    def validate_type(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Tipo é obrigatório.")
        return value.strip()


class ListExperimentSerializer(serializers.ModelSerializer):
    values = serializers.ListField(child=serializers.CharField())
    organization = OrganizationListSerializer(read_only=True)

    class Meta:
        model = ExperimentModel
        fields = "__all__"


class CreateFileModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileDataModel
        fields = ["id", "file_name", "file"]
        read_only_fields = ["id"]
