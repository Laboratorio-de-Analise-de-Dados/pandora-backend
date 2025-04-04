
from rest_framework import serializers

from analytics.models import DashboardModel, GateModel
from fcs_parser.models import FileDataModel
class GateSerializer(serializers.ModelSerializer):
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
        return GateSerializer(children, many=True).data


class ListGateSerializer(serializers.ModelSerializer):
    children = serializers.SerializerMethodField()
    file_data = serializers.PrimaryKeyRelatedField(
        queryset=FileDataModel.objects.all(),
        allow_null=True,
    )
    parent = serializers.PrimaryKeyRelatedField(queryset=GateModel.objects.all(), allow_null=True, required=False)
   

    class Meta:
        model = GateModel
        fields = ['id', 'created_at','parent', 'children', 'file_data', 'name', 'gate_coordinates']

    def get_children(self, obj):
         # Serializa os filhos do gate
        children = obj.children.all()
        return GateSerializer(children, many=True).data



class DashboardSerializer(serializers.ModelSerializer):
    gates = GateSerializer(many=True, read_only=True)

    class Meta:
        model = DashboardModel
        fields = ['id', 'name', 'experiment', 'file_data', 'dashboard_data', 'created_at', 'gates']