from django.conf import settings
from django.db import models

from analytics.gate_author import author_display_name
from fcs_parser.models import ExperimentModel, FileDataModel


# Create your models here.
class GateModel(models.Model):

    class Meta:
        db_table = "gate"
        constraints = [
            models.UniqueConstraint(
                fields=["name", "parent"],
                name="unique_gate_name_per_parent",
            ),
            models.UniqueConstraint(
                fields=["name", "file_data"],
                condition=models.Q(parent__isnull=True),
                name="unique_gate_name_root_level",
            ),
        ]

    file_data = models.ForeignKey(
        FileDataModel, related_name="gates", on_delete=models.CASCADE, null=True
    )
    name = models.CharField(max_length=50, db_index=True)
    gate_coordinates = models.JSONField(default=dict)
    # View config (eixos, escalas, limites, cutoff, modo) usada para exibir este
    # gate. Persiste por estratégia de gate e é clonada ao aplicar entre arquivos,
    # pra o usuário não voltar sempre pro FSC/SSC ao trocar de arquivo.
    plot_config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    dashboard = models.ForeignKey(
        "DashboardModel", related_name="gates", on_delete=models.CASCADE
    )
    parent = models.ForeignKey(
        "self", related_name="children", on_delete=models.CASCADE, null=True, blank=True
    )
    copied_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="copies",
    )
    color = models.CharField(max_length=7, null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gates_created",
    )

    def __str__(self) -> str:
        return f"Gate {self.id} – {self.name}"

    @classmethod
    def build_tree(cls, file_data_id):
        """
        Constrói uma estrutura de árvore de gates a partir de dados de um arquivo.
        """
        from analytics.models import AnalysisResult

        gates = list(
            cls.objects.filter(file_data_id=file_data_id).values(
                "id",
                "name",
                "parent_id",
                "gate_coordinates",
                "plot_config",
                "copied_from_id",
                "color",
                "created_at",
                "created_by_id",
                "created_by__first_name",
                "created_by__last_name",
                "created_by__username",
            )
        )

        # Busca analysis_result para todos os gates deste arquivo
        gate_ids = [g["id"] for g in gates]
        analysis_map = {}
        for ar in AnalysisResult.objects.filter(gate_id__in=gate_ids).values(
            "gate_id", "analysis_result"
        ):
            analysis_map[ar["gate_id"]] = ar["analysis_result"]

        # Cria um mapa de gates, preparando cada um para receber filhos
        gate_map = {}
        for gate in gates:
            author_name = author_display_name(
                gate.pop("created_by__first_name"),
                gate.pop("created_by__last_name"),
                gate.pop("created_by__username"),
            )
            entry = {
                **gate,
                "children": [],
                "created_by": gate.pop("created_by_id"),
                "created_by_name": author_name,
            }
            entry.pop("created_by_id", None)
            ar = analysis_map.get(gate["id"])
            if ar:
                entry["analysis_result"] = {"analysis_result": ar}
            gate_map[gate["id"]] = entry
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
        unique_together = ("name", "file_data")

    name = models.CharField(max_length=50)
    dashboard_config = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    file_data = models.ForeignKey(
        FileDataModel,
        related_name="dashboards",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    def __str__(self) -> str:
        return f"Dashboard {self.id} – {self.name}"


class AnalysisResult(models.Model):
    class Meta:
        db_table = "analysis_result"

    gate = models.OneToOneField(
        GateModel,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="analysis_result",
    )
    analysis_result = models.JSONField(default=dict)


class AnalysisRevision(models.Model):
    """Log append-only de mutações da estratégia de análise (BE-08, ADR-0008).

    Uma operação do usuário = uma revisão, mesmo quando toca N alvos
    (`affected_ids`). `payload_before`/`payload_after` guardam só os campos
    tocados por alvo — `{"<id>": {campo: valor}}` — e, para create/delete/
    apply, snapshots completos que viabilizam a reversão. O log nunca é
    editado nem apagado: reverter grava uma revisão nova com
    `action="revert"` e `reverts` apontando para a original.
    """

    class Meta:
        db_table = "analysis_revision"
        indexes = [
            models.Index(fields=["experiment", "-created_at"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    TARGET_GATE = "gate"
    TARGET_SUBSAMPLE = "subsample"
    TARGET_FILE = "file"
    TARGET_EXPERIMENT = "experiment"
    TARGET_COMPENSATION = "compensation"
    TARGET_CHOICES = [
        (TARGET_GATE, "Gate"),
        (TARGET_SUBSAMPLE, "Subsample"),
        (TARGET_FILE, "Amostra"),
        (TARGET_EXPERIMENT, "Experimento"),
        (TARGET_COMPENSATION, "Compensação"),
    ]

    ACTION_CREATE = "create"
    ACTION_UPDATE_GEOMETRY = "update_geometry"
    ACTION_RENAME = "rename"
    ACTION_RECOLOR = "recolor"
    ACTION_APPLY = "apply"
    ACTION_DELETE = "delete"
    ACTION_DISABLE = "disable"
    ACTION_ENABLE = "enable"
    ACTION_MOVE_SUBSAMPLE = "move_subsample"
    ACTION_REVERT = "revert"
    ACTION_RESTORE = "restore"
    ACTION_COMPENSATION_APPLY = "compensation_apply"
    ACTION_COMPENSATION_REMOVE = "compensation_remove"
    ACTION_DERIVE = "derive"
    ACTION_CHOICES = [
        (ACTION_CREATE, "Criação"),
        (ACTION_UPDATE_GEOMETRY, "Geometria"),
        (ACTION_RENAME, "Renomear"),
        (ACTION_RECOLOR, "Cor"),
        (ACTION_APPLY, "Aplicar em amostras"),
        (ACTION_DELETE, "Excluir"),
        (ACTION_DISABLE, "Desativar"),
        (ACTION_ENABLE, "Reativar"),
        (ACTION_MOVE_SUBSAMPLE, "Mover de subsample"),
        (ACTION_REVERT, "Reversão"),
        (ACTION_RESTORE, "Restauração de ponto"),
        (ACTION_COMPENSATION_APPLY, "Aplicar compensação"),
        (ACTION_COMPENSATION_REMOVE, "Remover compensação"),
        (ACTION_DERIVE, "Derivação de análise"),
    ]

    SCOPE_CHOICES = [
        ("file", "Amostra"),
        ("subsample", "Subsample"),
        ("experiment", "Experimento"),
    ]

    experiment = models.ForeignKey(
        ExperimentModel,
        on_delete=models.CASCADE,
        related_name="analysis_revisions",
    )
    target_type = models.CharField(max_length=20, choices=TARGET_CHOICES)
    target_id = models.BigIntegerField()
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    scope = models.CharField(
        max_length=20, choices=SCOPE_CHOICES, null=True, blank=True
    )
    payload_before = models.JSONField(default=dict, blank=True)
    payload_after = models.JSONField(default=dict, blank=True)
    affected_ids = models.JSONField(default=list, blank=True)
    summary = models.CharField(max_length=512)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analysis_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Amostra que a revisão toca — NULL = ação experiment-wide
    # (compensação, subsample, restore) que afeta todas as amostras e
    # aparece em qualquer recorte `?file=` da timeline.
    file_data = models.ForeignKey(
        "fcs_parser.FileDataModel",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analysis_revisions",
    )
    reverts = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reverted_by",
    )

    def __str__(self) -> str:
        return f"Revision {self.id} – {self.action} {self.target_type}:{self.target_id}"


class AnalysisCheckpoint(models.Model):
    """Marco nomeado sobre o log de análise (BE-20, ADR-0017).

    O checkpoint aponta para a revisão que fecha o ponto ("desfazer tudo
    depois dela"); `revision=None` marca o estado inicial do experimento
    (antes de qualquer revisão). Auto-checkpoints temporais são derivados
    na leitura — não viram linhas aqui. `active=False` é o descarte
    (soft delete, ADR-0005).
    """

    class Meta:
        db_table = "analysis_checkpoint"
        indexes = [models.Index(fields=["experiment", "-created_at"])]

    experiment = models.ForeignKey(
        ExperimentModel,
        on_delete=models.CASCADE,
        related_name="checkpoints",
    )
    revision = models.ForeignKey(
        AnalysisRevision,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="checkpoints",
    )
    message = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analysis_checkpoints",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        label = self.message or f"#{self.revision_id or 0}"
        return f"Checkpoint {self.id} – {label}"


class CompensationMatrix(models.Model):
    """Matriz de spillover de um experimento (BE-22, ADR-0018).

    ``matrix`` guarda S N×N (fração do fluorócromo j detectada no canal i,
    row-major) e ``channels`` a ordem dos eixos — ambos em JSON. O dado
    bruto nunca é reescrito: a aplicação acontece na leitura como
    ``S⁻¹ × eventos`` (ver ``fcs_parser/services/compensation.py``).

    ``is_applied`` marca a matriz ativa do experimento — invariante de
    "no máximo uma" vive no banco (UniqueConstraint condicional).
    ``active=False`` é o descarte (soft delete, ADR-0005).
    """

    SOURCE_FCS_HEADER = "fcs_header"
    SOURCE_COMPUTED = "computed"
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = [
        (SOURCE_FCS_HEADER, "Embutida no FCS"),
        (SOURCE_COMPUTED, "Calculada de controles"),
        (SOURCE_MANUAL, "Manual"),
    ]

    class Meta:
        db_table = "compensation_matrix"
        indexes = [models.Index(fields=["experiment", "-created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["experiment"],
                condition=models.Q(is_applied=True),
                name="unique_applied_compensation_per_experiment",
            )
        ]

    experiment = models.ForeignKey(
        ExperimentModel,
        on_delete=models.CASCADE,
        related_name="compensations",
    )
    name = models.CharField(max_length=256, blank=True, default="")
    channels = models.JSONField(default=list)
    matrix = models.JSONField(default=list)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    is_applied = models.BooleanField(default=False)
    active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_compensations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        label = self.name or self.get_source_display()
        return f"Compensation {self.id} – {label}"
