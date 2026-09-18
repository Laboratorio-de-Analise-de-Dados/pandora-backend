from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from accounts.models import Organization
from accounts.serializers import OrganizationListSerializer
from fcs_parser.permissions import can_create_experiment_type
from analytics.serializers import ListGateSerializer
from utils.validators import experiment_file_extension, validate_zip_file
from .models import (
    ExperimentModel,
    ExperimentTypeModel,
    FileDataModel,
    SampleTagModel,
    SubsampleModel,
)


def _extension_error_detail(exc: DjangoValidationError) -> str:
    return exc.messages[0]


def validate_experiment_context(user, title, org_id):
    """Unicidade de título no contexto + membership na organização.

    Compartilhado entre ``ExperimentInitSerializer`` (criação via upload) e
    ``ExperimentCreateSerializer`` (criação sem arquivo, BE-24).
    """
    if org_id is None:
        if ExperimentModel.objects.filter(
            title=title,
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
        return

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
        title=title, created_by=user, organization_id=org_id, active=True
    ).exists():
        raise serializers.ValidationError(
            {"detail": "Título já criado para esse laboratório."}
        )


class ExperimentTypeSerializer(serializers.ModelSerializer):
    """Vocabulário de tipos de experimento (BE-28).

    ``name`` é o casing canônico — o dedup acontece em
    ``name_normalized`` (lower + whitespace colapsado).
    """

    class Meta:
        model = ExperimentTypeModel
        fields = ["id", "name"]

    def validate_name(self, value):
        name = " ".join(value.split())
        if not name:
            raise serializers.ValidationError("Nome do tipo é obrigatório.")
        return name


class SubsampleSetupSerializer(serializers.Serializer):
    """Spec de subsample no setup inicial do experimento (BE-34).

    Sem validação de canal aqui: na criação do experimento ainda não
    há amostras — o canal é conferido contra os headers na primeira
    edição do subsample.
    """

    name = serializers.CharField()
    control_type = serializers.ChoiceField(
        choices=SubsampleModel.CONTROL_TYPE_CHOICES,
        required=False,
        allow_null=True,
        default=None,
    )
    control_channel = serializers.CharField(
        required=False, allow_blank=True, default=""
    )

    def validate_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Nome do subsample é obrigatório.")
        return name


class ExperimentCreateSerializer(serializers.Serializer):
    """Entrada de POST /experiment/ — cria experimento sem arquivo (BE-24).

    O experimento nasce ``status="new"``/``file_status="pending"`` (defaults
    do modelo); as amostras chegam depois via ``/experiment/<id>/files/init``.
    ``subsamples`` (BE-34) permite já criar os grupos no setup — ex.: o
    subsample de controles antes do upload da placa.
    """

    title = serializers.CharField()
    type = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True, default="")
    organizationId = serializers.IntegerField(required=False, allow_null=True)
    subsamples = SubsampleSetupSerializer(many=True, required=False)

    def validate_title(self, value):
        title = value.strip().replace(" ", "_")
        if not title:
            raise serializers.ValidationError("Título é obrigatório.")
        return title

    def validate_type(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Tipo é obrigatório.")
        return value.strip()

    def validate(self, data):
        validate_experiment_context(
            self.context["request"].user, data["title"], data.get("organizationId")
        )
        return data


class ExperimentInitSerializer(serializers.Serializer):
    """Entrada de POST /experiment/init/ — cria o experimento reserva.

    Além de formato, valida unicidade de título no contexto (pessoal vs
    organização) e membership na organização de destino.
    """

    title = serializers.CharField()
    type = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True, default="")
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
        validate_experiment_context(
            self.context["request"].user, data["title"], data.get("organizationId")
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


class SampleTagSerializer(serializers.ModelSerializer):
    """Vocabulário de tags de amostra (BE-34).

    Na leitura expõe a semântica completa; na escrita só ``name``,
    ``color`` e ``organization`` são aceitos — ``system_key``,
    ``category="control"`` e ``scope`` são deduzidos na view (tags de
    usuário nunca viram de sistema por API).
    """

    class Meta:
        model = SampleTagModel
        fields = [
            "id",
            "name",
            "system_key",
            "category",
            "color",
            "scope",
            "organization",
        ]
        read_only_fields = ["id", "system_key", "category", "scope"]

    def validate_name(self, value):
        name = " ".join(value.split())
        if not name:
            raise serializers.ValidationError("Nome da tag é obrigatório.")
        return name

    def validate_color(self, value):
        import re

        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value or ""):
            raise serializers.ValidationError("Cor inválida — use hex #RRGGBB.")
        return value.lower()


class SubsampleSerializer(serializers.ModelSerializer):
    files_count = serializers.SerializerMethodField()
    # BE-34: tags de contexto do grupo (leitura = objetos; escrita = ids
    # em ``tag_ids``). Só ``category="general"`` — controle do grupo é
    # ``control_type``, validado em ``set_subsample_tags``.
    tags = SampleTagSerializer(many=True, read_only=True)
    tag_ids = serializers.ListField(
        child=serializers.IntegerField(), write_only=True, required=False
    )

    class Meta:
        model = SubsampleModel
        fields = [
            "id",
            "name",
            "source_path",
            "active",
            "created_at",
            "files_count",
            "control_type",
            "control_channel",
            "tags",
            "tag_ids",
        ]
        read_only_fields = ["id", "source_path", "active", "created_at"]

    def get_files_count(self, obj) -> int:
        return obj.files.filter(active=True).count()

    def validate_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError("Nome do subsample é obrigatório.")
        return name

    def validate_tag_ids(self, value):
        from fcs_parser.services.tags import resolve_tags

        # Resolve já validando visibilidade/exclusividade; a lista
        # resolvida vai para ``create``/``update`` via contexto.
        self.context["resolved_tags"] = resolve_tags(
            value, self.context["request"].user, allow_control=False
        )
        return value

    def create(self, validated_data):
        resolved = self.context.pop("resolved_tags", None)
        validated_data.pop("tag_ids", None)
        instance = super().create(validated_data)
        if resolved is not None:
            instance.tags.set(resolved)
        return instance

    def update(self, instance, validated_data):
        resolved = self.context.pop("resolved_tags", None)
        validated_data.pop("tag_ids", None)
        instance = super().update(instance, validated_data)
        if resolved is not None:
            instance.tags.set(resolved)
        return instance

    def validate(self, attrs):
        """Validação de controle de compensação (BE-22, ADR-0019)."""
        control_type = attrs.get(
            "control_type", getattr(self.instance, "control_type", None)
        )
        control_channel = attrs.get(
            "control_channel", getattr(self.instance, "control_channel", "")
        )
        if not control_type:
            attrs["control_channel"] = ""
            return attrs

        experiment = (
            self.instance.experiment
            if self.instance is not None
            else self.context["experiment"]
        )
        siblings = SubsampleModel.objects.filter(
            experiment=experiment, active=True, control_type__isnull=False
        )
        if self.instance is not None:
            siblings = siblings.exclude(pk=self.instance.pk)

        if control_type == SubsampleModel.CONTROL_UNSTAINED:
            if siblings.filter(control_type=SubsampleModel.CONTROL_UNSTAINED).exists():
                raise serializers.ValidationError(
                    {
                        "control_type": "Já existe um controle negativo neste experimento."
                    }
                )
            attrs["control_channel"] = ""
            return attrs

        # single_stain: canal obrigatório, fluorescente e exclusivo.
        channel = (control_channel or "").strip()
        if not channel:
            raise serializers.ValidationError(
                {"control_channel": "Informe o canal que este controle cora."}
            )
        from fcs_parser.services.compensation import fluorescent_channels
        from utils.density import normalize_column_name

        valid = {normalize_column_name(c) for c in fluorescent_channels(experiment)}
        if normalize_column_name(channel) not in valid:
            raise serializers.ValidationError(
                {
                    "control_channel": (
                        f"'{channel}' não é um canal fluorescente do experimento."
                    )
                }
            )
        clash = siblings.filter(
            control_type=SubsampleModel.CONTROL_SINGLE_STAIN
        ).exclude(control_channel="")
        if any(
            normalize_column_name(s.control_channel) == normalize_column_name(channel)
            for s in clash
        ):
            raise serializers.ValidationError(
                {"control_channel": f"Já existe um controle para '{channel}'."}
            )
        attrs["control_channel"] = channel
        return attrs


class FileTagsUpdateSerializer(serializers.Serializer):
    """Entrada de PUT /experiment/file/<id>/tags — conjunto completo."""

    tags = serializers.ListField(
        child=serializers.IntegerField(), required=True, allow_empty=True
    )


class ListFileDataSerializer(serializers.ModelSerializer):

    gates = ListGateSerializer(many=True, read_only=True)
    # BE-22: a amostra traz $SPILLOVER/$COMP nos headers? O front usa para
    # marcar o arquivo com um indicador de compensação disponível.
    has_embedded_compensation = serializers.SerializerMethodField()
    # BE-34: chips semânticos. `tags` é o M2M (prefetch no queryset);
    # `inherited_tags` vem do subsample (herança virtual — tag explícita
    # vence a herdada); `suggested_tags` é heurística por filename.
    tags = SampleTagSerializer(many=True, read_only=True)
    inherited_tags = serializers.SerializerMethodField()
    suggested_tags = serializers.SerializerMethodField()

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
            "has_embedded_compensation",
            "tags",
            "inherited_tags",
            "suggested_tags",
        ]
        read_only_fields = ["id", "source_path", "active", "deactivated_at"]

    def get_has_embedded_compensation(self, obj) -> bool:
        from fcs_parser.services.compensation import parse_spillover

        return parse_spillover(obj.headers) is not None

    def get_inherited_tags(self, obj) -> list:
        from fcs_parser.services.tags import inherited_tags

        return SampleTagSerializer(
            inherited_tags(obj), many=True, context=self.context
        ).data

    def get_suggested_tags(self, obj) -> list[str]:
        from fcs_parser.services.tags import suggest_tags

        return suggest_tags(obj.file_name)


class ParamListDataSerializer(serializers.ModelSerializer):
    gates = ListGateSerializer(many=True, read_only=True)

    class Meta:
        model = FileDataModel
        fields = ["id", "file_name", "data_set", "gates"]


class UpdateExperimentSerializer(serializers.ModelSerializer):
    """Escrita de experimento: só os campos que o usuário pode corrigir.

    ``values`` (canais) ficou fora de propósito (BE-24): é derivado dos
    arquivos — a extração sobrescreve a cada upload, então edição manual
    só produziria dessincronia transitória.
    """

    class Meta:
        model = ExperimentModel
        fields = ["title", "type", "description"]

    def validate_title(self, value):
        title = value.strip().replace(" ", "_")
        if not title:
            raise serializers.ValidationError("Título é obrigatório.")
        return title

    def validate_type(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Tipo é obrigatório.")
        value = value.strip()
        # BE-28/ADR-0023: tipo novo entra no vocabulário no save() — quem
        # pode introduzir um é só admin ou o dono do experimento; membro
        # editando experimento alheio escolhe entre os existentes.
        exists = ExperimentTypeModel.objects.filter(
            name_normalized=ExperimentTypeModel.normalize(value)
        ).exists()
        if not exists:
            request = self.context.get("request")
            user = getattr(request, "user", None)
            if not (user and can_create_experiment_type(user, self.instance)):
                raise serializers.ValidationError(
                    "Tipo inexistente — criar um tipo novo exige ser dono "
                    "do experimento ou admin."
                )
        return value


class ListExperimentSerializer(serializers.ModelSerializer):
    values = serializers.ListField(child=serializers.CharField())
    organization = OrganizationListSerializer(read_only=True)
    created_by_name = serializers.CharField(
        source="created_by.username", read_only=True, default=None
    )
    # BE-21: metadados visuais da listagem. `my_role`/`preview_available`
    # chegam anotados no queryset (sem N+1); `progress` é derivado dos
    # campos de upload do próprio modelo.
    my_role = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    preview_available = serializers.SerializerMethodField()
    compensated = serializers.SerializerMethodField()

    class Meta:
        model = ExperimentModel
        fields = "__all__"

    def get_my_role(self, obj):
        return getattr(obj, "my_role", None)

    def get_progress(self, obj):
        if obj.status == "uploading" and obj.total_chunks:
            return round(len(obj.received_chunks or []) / obj.total_chunks * 100)
        return None

    def get_preview_available(self, obj):
        return getattr(obj, "preview_available", False)

    def get_compensated(self, obj):
        return getattr(obj, "compensated", False)


class CreateFileModelSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileDataModel
        fields = ["id", "file_name", "file"]
        read_only_fields = ["id"]
