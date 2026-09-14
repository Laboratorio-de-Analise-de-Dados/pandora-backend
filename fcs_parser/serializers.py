from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from accounts.models import Organization
from accounts.serializers import OrganizationListSerializer
from analytics.serializers import ListGateSerializer
from utils.validators import experiment_file_extension, validate_zip_file
from .models import ExperimentModel, FileDataModel, SubsampleModel


def _extension_error_detail(exc: DjangoValidationError) -> str:
    return exc.messages[0]


class ExperimentInitSerializer(serializers.Serializer):
    """Entrada de POST /experiment/init/ — cria o experimento reserva.

    Além de formato, valida unicidade de título no contexto (pessoal vs
    organização) e membership na organização de destino.
    """

    title = serializers.CharField()
    type = serializers.CharField()
    totalChunks = serializers.IntegerField(min_value=1)
    fileName = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    organizationId = serializers.IntegerField(required=False, allow_null=True)

    def validate_title(self, value):
        title = value.strip().replace(" ", "_")
        if not title:
            raise serializers.ValidationError("Título é obrigatório.")
        return title

    def validate_type(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Tipo é obrigatório.")
        return value.strip()

    def validate_fileName(self, value):
        if value in (None, ""):
            return value
        try:
            experiment_file_extension(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_extension_error_detail(exc))
        return value

    def validate(self, data):
        user = self.context["request"].user
        org_id = data.get("organizationId")

        if org_id is None:
            if ExperimentModel.objects.filter(
                title=data["title"],
                created_by=user,
                organization__isnull=True,
                active=True,
            ).exists():
                raise serializers.ValidationError(
                    {
                        "detail": (
                            "Você já possui um experimento pessoal com este " "título."
                        )
                    }
                )
            return data

        if not Organization.objects.filter(id=org_id).exists():
            raise serializers.ValidationError({"detail": "Laboratório não encontrado."})
        if (
            not user.is_super_admin
            and not user.memberships.filter(
                organization_id=org_id, status="active"
            ).exists()
        ):
            raise PermissionDenied(
                "Você não tem permissão para criar experimentos neste " "laboratório."
            )
        if ExperimentModel.objects.filter(
            title=data["title"], created_by=user, organization_id=org_id, active=True
        ).exists():
            raise serializers.ValidationError(
                {"detail": "Título já criado para esse laboratório."}
            )
        return data


class ExperimentFileInitSerializer(serializers.Serializer):
    """Entrada de POST /experiment/<id>/files/init — upload anexado."""

    fileName = serializers.CharField()
    totalChunks = serializers.IntegerField(min_value=1)

    def validate_fileName(self, value):
        try:
            experiment_file_extension(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_extension_error_detail(exc))
        return value


class ChunkUploadSerializer(serializers.Serializer):
    """Entrada dos endpoints de chunk (experimento e arquivo)."""

    fileId = serializers.IntegerField()
    chunkIndex = serializers.IntegerField(min_value=0)
    chunk = serializers.FileField()


class ExperimentCompleteSerializer(serializers.Serializer):
    """Entrada de POST /experiment/complete/ e /experiment/files/complete/."""

    fileId = serializers.IntegerField()
    fileName = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate_fileName(self, value):
        if value in (None, ""):
            return value
        try:
            experiment_file_extension(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(_extension_error_detail(exc))
        return value


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
