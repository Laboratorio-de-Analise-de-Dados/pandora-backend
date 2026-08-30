
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from analytics.gate_scope import SCOPE_CHOICES, SCOPE_EXPERIMENT, SCOPE_FILE
from analytics.models import AnalysisResult, DashboardModel, GateModel
from fcs_parser.models import FileDataModel


class DashboardSerializer(serializers.ModelSerializer):
    class Meta:
        model = DashboardModel
        fields = ['id', 'name', 'dashboard_config', 'created_at', 'file_data']
        validators = []  # disable auto UniqueTogetherValidator; handled in create()

    def create(self, validated_data):
        dashboard_instance, created = DashboardModel.objects.update_or_create(
            name=validated_data['name'],
            file_data=validated_data['file_data'],
            defaults={'dashboard_config': validated_data.get('dashboard_config', {})},
        )
        return dashboard_instance
        
class GateSerializer(serializers.ModelSerializer):
    file_data = serializers.PrimaryKeyRelatedField(
        queryset=FileDataModel.objects.all(),
        allow_null=True,
    )
    dashboard = serializers.PrimaryKeyRelatedField(
        queryset= DashboardModel.objects.all(),
        required=True, 
        allow_null=False
    ) 
    parent = serializers.PrimaryKeyRelatedField(queryset=GateModel.objects.all(), allow_null=True, required=False, default=None)
    class Meta: 
        model = GateModel
        fields = [
            'id', 'name', 'gate_coordinates', 'plot_config', 'created_at', 
            'dashboard',
            'file_data', 'parent', 'copied_from', 'color',
        ]
        read_only_fields = ['id', 'created_at'] 

    def create(self, validated_data):
        file_data_instance = validated_data.get('file_data') 
        if not file_data_instance:
            raise serializers.ValidationError({"file_data": "File data is required to create or associate a dashboard."})

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
                        'Só é aceito com scope="experiment"; no escopo do arquivo a '
                        "exclusão atinge apenas os gates informados."
                    )
                }
            )
        return data


class GateUpdateSerializer(serializers.Serializer):
    """Payload de PATCH /analytics/gate/<gate_id>.

    `scope="experiment"` propaga nome e cor para as cópias do gate nas demais
    amostras do experimento. Geometria e `plot_config` nunca são propagados.
    """

    name = serializers.CharField(max_length=50, required=False)
    color = serializers.CharField(max_length=7, required=False, allow_blank=True)
    gate_coordinates = serializers.JSONField(required=False)
    plot_config = serializers.JSONField(required=False)
    scope = serializers.ChoiceField(choices=SCOPE_CHOICES, default=SCOPE_FILE)

    def validate(self, data):
        if data["scope"] == SCOPE_EXPERIMENT and not (
            "name" in data or "color" in data
        ):
            raise serializers.ValidationError(
                {"scope": 'scope="experiment" exige "name" e/ou "color".'}
            )
        return data


class AnalysisResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnalysisResult
        fields = ['analysis_result'] 
        read_only_fields = ['id', 'gate']

class ListGateSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()
    file_data = serializers.PrimaryKeyRelatedField(
        queryset=FileDataModel.objects.all(),
        allow_null=True,
    )
    parent_id = serializers.PrimaryKeyRelatedField(
        source="parent", queryset=GateModel.objects.all(), allow_null=True, required=False
    )
    analysis_result = AnalysisResultSerializer(read_only=True)
    depth = 1
    
    copied_from_id = serializers.PrimaryKeyRelatedField(
        source="copied_from", read_only=True
    )

    class Meta:
        model = GateModel
        fields = ['id', 'created_at', 'parent_id', 'children', 'file_data', 'name', 'gate_coordinates', 'plot_config', 'analysis_result', 'copied_from_id', 'color']

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_children(self, obj):
         # Serializa os filhos do gate
        children = obj.children.all()
        return GateSerializer(children, many=True).data



