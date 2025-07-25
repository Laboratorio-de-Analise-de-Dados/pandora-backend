from django.db import models

from fcs_parser.models import ExperimentModel, FileDataModel

# Create your models here.
class GateModel(models.Model):

    class Meta:
        db_table = "gate"
        unique_together = ('name', 'file_data')

    file_data = models.ForeignKey(
        FileDataModel, related_name="gates", on_delete=models.CASCADE, null=True
    )
    name = models.CharField(max_length=50, db_index=True)
    gate_coordinates = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    dashboard = models.ForeignKey(
        "DashboardModel", related_name="gates", on_delete=models.CASCADE
    )
    parent = models.ForeignKey(
        "self", related_name="children", on_delete=models.CASCADE, null=True, blank=True
    )

    @classmethod
    def build_tree(cls, file_data_id):
        
        gates = cls.objects.filter(file_data_id=file_data_id).values(
            "id", "name", "parent_id", "gate_coordinates"
        )
        
     
        gate_map = {gate["id"]: {**gate, "children": []} for gate in gates}

      
        roots = []

        # Monta a árvore
        for gate in gates:
            parent_id = gate["parent_id"]
            if parent_id:
                gate_map[parent_id]["children"].append(gate_map[gate["id"]])
            else:
                roots.append(gate_map[gate["id"]])

        return roots



class DashboardModel(models.Model):

    class Meta:
        db_table = "dashboard"
        unique_together = ('name', 'file_data')

    name = models.CharField(max_length=50)
    dashboard_config = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    file_data = models.ForeignKey(
        FileDataModel, related_name="dashboards", on_delete=models.CASCADE, null=True, blank=True
    )

class AnalysisResult(models.Model):
    class Meta:
        db_table = 'analysis_result'
        
    gate = models.OneToOneField(GateModel, on_delete=models.CASCADE, primary_key=True, related_name='analysis_result')
    analysis_result = models.JSONField(default=dict)
