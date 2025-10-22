# seu_projeto_django/analytics/signals.py

from django.db.models.signals import post_save
from django.dispatch import receiver
from analytics.tasks import recalculate_gate_analysis_task
from .models import GateModel # Importe seu GateModel

@receiver(post_save, sender=GateModel)
def trigger_gate_analysis_recalculation(sender, instance, created, **kwargs):
    """
    Dispara a tarefa Celery para recalcular os resultados de análise do gate
    e seus filhos após o GateModel ser salvo/atualizado.
    """
    print(f"Signal: Disparada tarefa de recálculo para Gate '{instance.name}' (ID: {instance.id}).")
    recalculate_gate_analysis_task.delay(instance.id)