from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from analytics.gate_author import author_display_name
from analytics.gate_scope import PROPAGATING_SCOPES, SCOPE_CHOICES, SCOPE_FILE
from analytics.models import (
    AnalysisBranch,
    AnalysisCheckpoint,
    AnalysisResult,
    AnalysisRevision,
    CompensationMatrix,
    DashboardModel,
    GateModel,
)
from fcs_parser.models import FileDataModel


def gate_author_name(gate):
    """Nome do autor do gate para exibição, ou None para gates antigos."""
    author = gate.created_by
    if not author:
        return None
    return author_display_name(author.first_name, author.last_name, author.username)


class DashboardSerializer(serializers.ModelSerializer):
    class Meta:
        model = DashboardModel
        fields = ["id", "name", "dashboard_config", "created_at", "file_data"]
        validators = []  # disable auto UniqueTogetherValidator; handled in create()

    def create(self, validated_data):
        dashboard_instance, created = DashboardModel.objects.update_or_create(
            name=validated_data["name"],
            file_data=validated_data["file_data"],
            defaults={"dashboard_config": validated_data.get("dashboard_config", {})},
        )
        return dashboard_instance


class GateSerializer(serializers.ModelSerializer):
    file_data = serializers.PrimaryKeyRelatedField(
        queryset=FileDataModel.objects.all(),
        allow_null=True,
    )
    dashboard = serializers.PrimaryKeyRelatedField(
        queryset=DashboardModel.objects.all(), required=True, allow_null=False
    )
    parent = serializers.PrimaryKeyRelatedField(
        queryset=GateModel.objects.all(), allow_null=True, required=False, default=None
    )
    branch = serializers.PrimaryKeyRelatedField(
        queryset=AnalysisBranch.objects.all(),
        required=False,
        allow_null=True,
        default=None,  # unique-together exige default p/ não forçar required
    )
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = GateModel
        fields = [
            "id",
            "name",
            "gate_coordinates",
            "plot_config",
            "created_at",
            "dashboard",
            "file_data",
            "parent",
            "copied_from",
            "branch",
            "color",
            "created_by",
            "created_by_name",
        ]
        read_only_fields = ["id", "created_at", "created_by"]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_created_by_name(self, obj):
        return gate_author_name(obj)

    def validate(self, attrs):
        parent = attrs.get("parent")
        branch = attrs.get("branch")
        file_data = attrs.get("file_data")
        if parent is not None:
            # Filho herda a branch do pai — nunca se mistura linhas numa árvore.
            if branch is not None and branch.id != parent.branch_id:
                raise serializers.ValidationError(
                    {"branch": "O gate filho pertence à branch do pai."}
                )
            attrs["branch"] = parent.branch
        elif branch is not None and file_data is not None:
            if branch.experiment_id != file_data.experiment_id:
                raise serializers.ValidationError(
                    {"branch": "A branch pertence a outro experimento."}
                )
        return attrs

    def create(self, validated_data):
        file_data_instance = validated_data.get("file_data")
        if not file_data_instance:
            raise serializers.ValidationError(
                {
                    "file_data": "File data is required to create or associate a dashboard."
                }
            )

        gate = GateModel.objects.create(**validated_data)

        return gate

    def get_children(self, obj):
        # Serializa os filhos do gate
        children = obj.children.all()
        return GateSerializer(children, many=True).data


class GateBatchDeleteSerializer(serializers.Serializer):
    """Payload de POST /analytics/gate/delete-batch."""

    source_gate_ids = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=False
    )
    scope = serializers.ChoiceField(choices=SCOPE_CHOICES, default=SCOPE_FILE)
    target_file_data_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )
    recursive = serializers.BooleanField(default=True)
    include_source = serializers.BooleanField(default=False)

    def validate(self, data):
        if data["scope"] == SCOPE_FILE and data["target_file_data_ids"]:
            raise serializers.ValidationError(
                {
                    "target_file_data_ids": (
                        'Só é aceito com scope="experiment" ou "subsample"; no '
                        "escopo do arquivo a exclusão atinge apenas os gates "
                        "informados."
                    )
                }
            )
        return data


class GateUpdateSerializer(serializers.Serializer):
    """Payload de PATCH /analytics/gate/<gate_id>.

    `scope="experiment"` propaga nome, cor e geometria para as cópias do gate
    nas demais amostras do experimento; `scope="subsample"` restringe isso às
    amostras do subsample da amostra alvo. `plot_config` nunca é propagado.

    Com `dry_run=True` nada é gravado e a resposta lista as amostras que
    seriam alteradas, para a UI confirmar antes.
    """

    name = serializers.CharField(max_length=50, required=False)
    color = serializers.CharField(max_length=7, required=False, allow_blank=True)
    gate_coordinates = serializers.JSONField(required=False)
    plot_config = serializers.JSONField(required=False)
    scope = serializers.ChoiceField(choices=SCOPE_CHOICES, default=SCOPE_FILE)
    dry_run = serializers.BooleanField(default=False)

    def validate(self, data):
        if data["scope"] in PROPAGATING_SCOPES and not (
            "name" in data or "color" in data or "gate_coordinates" in data
        ):
            raise serializers.ValidationError(
                {
                    "scope": (
                        f'scope="{data["scope"]}" exige "name", "color" e/ou '
                        '"gate_coordinates".'
                    )
                }
            )
        return data


class AnalysisResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnalysisResult
        fields = ["analysis_result"]
        read_only_fields = ["id", "gate"]


class ListGateSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()
    file_data = serializers.PrimaryKeyRelatedField(
        queryset=FileDataModel.objects.all(),
        allow_null=True,
    )
    parent_id = serializers.PrimaryKeyRelatedField(
        source="parent",
        queryset=GateModel.objects.all(),
        allow_null=True,
        required=False,
    )
    analysis_result = AnalysisResultSerializer(read_only=True)
    depth = 1

    copied_from_id = serializers.PrimaryKeyRelatedField(
        source="copied_from", read_only=True
    )
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = GateModel
        fields = [
            "id",
            "created_at",
            "parent_id",
            "children",
            "file_data",
            "name",
            "gate_coordinates",
            "plot_config",
            "analysis_result",
            "copied_from_id",
            "branch_id",
            "color",
            "created_by",
            "created_by_name",
        ]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_created_by_name(self, obj):
        return gate_author_name(obj)

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_children(self, obj):
        # Serializa os filhos do gate
        children = obj.children.all()
        return GateSerializer(children, many=True).data


class AnalysisRevisionSerializer(serializers.ModelSerializer):
    """Item do histórico (BE-08): frase pronta + alvo + autor."""

    author = serializers.SerializerMethodField()
    revertible = serializers.SerializerMethodField()
    target = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisRevision
        fields = [
            "id",
            "action",
            "scope",
            "branch",
            "target",
            "file_data",
            "summary",
            "author",
            "affected_ids",
            "created_at",
            "reverts",
            "revertible",
        ]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_author(self, obj):
        if not obj.user:
            return None
        return author_display_name(
            obj.user.first_name, obj.user.last_name, obj.user.username
        )

    @extend_schema_field(serializers.BooleanField())
    def get_revertible(self, obj):
        from analytics.history import REVERSIBLE_ACTIONS

        return obj.action in REVERSIBLE_ACTIONS

    @extend_schema_field(serializers.DictField())
    def get_target(self, obj):
        return {"type": obj.target_type, "id": obj.target_id}


class AnalysisRevisionDetailSerializer(AnalysisRevisionSerializer):
    """Detalhe da revisão: payloads completos para o diff antes/depois."""

    class Meta(AnalysisRevisionSerializer.Meta):
        fields = AnalysisRevisionSerializer.Meta.fields + [
            "payload_before",
            "payload_after",
        ]


class RevertRevisionSerializer(serializers.Serializer):
    """Payload de POST /analytics/history/<id>/revert/."""

    dry_run = serializers.BooleanField(default=False)


class RestoreSerializer(serializers.Serializer):
    """Payload de POST .../restore/ (revisão ou checkpoint como alvo)."""

    dry_run = serializers.BooleanField(default=False)
    force = serializers.BooleanField(default=False)


class AnalysisCheckpointSerializer(serializers.ModelSerializer):
    """Marco nomeado sobre o log (BE-20/ADR-0017)."""

    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisCheckpoint
        fields = [
            "id",
            "revision",
            "message",
            "created_by_name",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_created_by_name(self, obj):
        if not obj.created_by:
            return None
        return author_display_name(
            obj.created_by.first_name,
            obj.created_by.last_name,
            obj.created_by.username,
        )


class CheckpointCreateSerializer(serializers.Serializer):
    """POST .../checkpoints/ — `revision_id` fixa uma borda passada (pin)."""

    message = serializers.CharField(max_length=200, required=False, allow_blank=True)
    revision_id = serializers.IntegerField(required=False, allow_null=True)


class CheckpointPatchSerializer(serializers.Serializer):
    """PATCH .../checkpoints/<id>/ — só a mensagem é editável."""

    message = serializers.CharField(max_length=200, allow_blank=True)


class AnalysisBranchSerializer(serializers.ModelSerializer):
    """Linha de análise do experimento (BE-23)."""

    created_by_name = serializers.SerializerMethodField()
    gates_count = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisBranch
        fields = [
            "id",
            "name",
            "is_main",
            "base_branch",
            "created_by_name",
            "gates_count",
            "created_at",
        ]
        read_only_fields = fields

    def get_created_by_name(self, obj):
        if not obj.created_by:
            return None
        return author_display_name(
            obj.created_by.first_name,
            obj.created_by.last_name,
            obj.created_by.username,
        )

    def get_gates_count(self, obj):
        return obj.gates.count()


class BranchCreateSerializer(serializers.Serializer):
    """POST .../branches/ — fork materializado da branch base."""

    name = serializers.CharField(max_length=50)
    base_branch_id = serializers.IntegerField(required=False, allow_null=True)


class BranchRenameSerializer(serializers.Serializer):
    """PATCH /analytics/branches/<id>/ — só o nome é editável."""

    name = serializers.CharField(max_length=50)


class BranchMergeSerializer(serializers.Serializer):
    """POST /analytics/branches/<id>/merge/ — merge da branch na base.

    ``resolutions`` mapeia a chave de cada conflito do diff
    (``f:<gate_id>``, ``dt:<gate_id>``, ``ds:<gate_id>``) para
    ``mine`` (fica a base), ``theirs`` (vale a branch) ou ``both``
    (mantém a base e cria a versão da branch renomeada — só
    ``modified_both``).
    """

    resolutions = serializers.DictField(
        child=serializers.ChoiceField(choices=["mine", "theirs", "both"]),
        required=False,
        default=dict,
    )
    dry_run = serializers.BooleanField(default=False)

    def validate(self, data):
        for key in data["resolutions"]:
            prefix = key.split(":", 1)[0]
            if prefix not in ("f", "dt", "ds"):
                raise serializers.ValidationError(
                    {"resolutions": f'Chave de conflito inválida: "{key}".'}
                )
        for key, res in data["resolutions"].items():
            if res == "both" and not key.startswith("f:"):
                raise serializers.ValidationError(
                    {"resolutions": '"both" só vale para conflitos de edição.'}
                )
        return data


class CompensationMatrixSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = CompensationMatrix
        fields = [
            "id",
            "name",
            "channels",
            "matrix",
            "source",
            "is_applied",
            "created_by_name",
            "created_at",
        ]

    def get_created_by_name(self, obj):
        author = obj.created_by
        if not author:
            return None
        return author_display_name(author.first_name, author.last_name, author.username)


class CompensationComputeSerializer(serializers.Serializer):
    """Override explícito do mapeamento canal→controle (ADR-0019).

    Sem payload, o compute deriva dos subsamples marcados; quando
    ``controls``/``negative`` vêm, substituem a descoberta. As chaves de
    ``controls`` precisam ser canais fluorescentes do experimento — a
    mesma regra que vale para ``control_channel`` de subsample, senão a
    matriz sai com uma coluna que nunca acha evento.
    """

    name = serializers.CharField(required=False, allow_blank=True, max_length=256)
    negative = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False
    )
    controls = serializers.DictField(
        child=serializers.ListField(child=serializers.IntegerField(min_value=1)),
        required=False,
    )

    def validate(self, attrs):
        controls = attrs.get("controls")
        if not controls:
            return attrs
        experiment = self.context.get("experiment")
        if experiment is None:
            return attrs
        from fcs_parser.services.compensation import fluorescent_channels
        from utils.density import normalize_column_name

        valid = {normalize_column_name(c) for c in fluorescent_channels(experiment)}
        invalid = [
            channel
            for channel in controls
            if normalize_column_name(channel) not in valid
        ]
        if invalid:
            raise serializers.ValidationError(
                {
                    "controls": (
                        "Canais não fluorescentes do experimento: "
                        + ", ".join(sorted(invalid))
                    )
                }
            )
        return attrs
