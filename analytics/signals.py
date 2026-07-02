from django.db.models.signals import post_save
from django.dispatch import receiver
from analytics.tasks import recalculate_gate_analysis
from utils.density import invalidate_density
from .models import GateModel


@receiver(post_save, sender=GateModel)
def trigger_gate_analysis_recalculation(sender, instance, created, **kwargs):
    """Recalcula os resultados de análise do gate e seus filhos após salvar."""
    invalidate_density(instance.file_data_id)
    recalculate_gate_analysis(instance.id)
