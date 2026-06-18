
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

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
            'id', 'name', 'gate_coordinates', 'created_at', 
            'dashboard',
            'file_data', 'parent', 'copied_from',
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
        fields = ['id', 'created_at', 'parent_id', 'children', 'file_data', 'name', 'gate_coordinates', 'analysis_result', 'copied_from_id']

    @extend_schema_field(serializers.ListField(child=serializers.DictField()))
    def get_children(self, obj):
         # Serializa os filhos do gate
        children = obj.children.all()
        return GateSerializer(children, many=True).data



