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
        """
        Constrói uma estrutura de árvore de gates a partir de dados de um arquivo.
        """
        gates = cls.objects.filter(file_data_id=file_data_id).values(
            "id", "name", "parent_id", "gate_coordinates"
        )

        # Cria um mapa de gates, preparando cada um para receber filhos
        gate_map = {gate["id"]: {**gate, "children": []} for gate in gates}
        roots = []

        # Percorre todos os gates para construir a hierarquia
        for gate in gates:
            gate_obj = gate_map[gate["id"]]
            parent_id = gate["parent_id"]
            if parent_id is not None:
                # Se tem um pai (parent_id), ele é filho de outro gate
                if parent_id in gate_map:
                    gate_map[parent_id]["children"].append(gate_obj)
            else:
                # Se o parent_id é null, ele é uma raiz (filho direto do arquivo)
                roots.append(gate_obj)
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
