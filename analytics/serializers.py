
from rest_framework import serializers

from analytics.models import AnalysisResult, DashboardModel, GateModel
from fcs_parser.models import FileDataModel


class DashboardSerializer(serializers.ModelSerializer):
    class Meta:
        model = DashboardModel
        fields = ['id', 'name', 'dashboard_config', 'created_at']
    def create(self, validated_data):
        name = validated_data.get('name')
        dashboard_instance, created = DashboardModel.objects.update_or_create(
            **validated_data
        )
        print(f"Dashboard '{dashboard_instance.name}' {'criado' if created else 'atualizado'} via Serializer.")
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
    parent = serializers.PrimaryKeyRelatedField(queryset=GateModel.objects.all(), allow_null=True, required=False)
    class Meta: 
        model = GateModel
        fields = [
            'id', 'name', 'gate_coordinates', 'created_at', 
            'dashboard',
            'file_data', 'parent', 
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
    parent = serializers.PrimaryKeyRelatedField(queryset=GateModel.objects.all(), allow_null=True, required=False)
    analysis_result = AnalysisResultSerializer(read_only=True)
    depth = 1
    
    class Meta:
        model = GateModel
        fields = ['id', 'created_at','parent', 'children', 'file_data', 'name', 'gate_coordinates', 'analysis_result']

    def get_children(self, obj):
         # Serializa os filhos do gate
        children = obj.children.all()
        return GateSerializer(children, many=True).data



