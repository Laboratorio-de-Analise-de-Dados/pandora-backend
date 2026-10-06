from django.conf import settings
from django.db import models

from analytics.gate_author import author_display_name
from fcs_parser.models import ExperimentModel, FileDataModel, SubsampleModel


class AnalysisBranch(models.Model):
    """Linha de trabalho nomeada dentro de um experimento (BE-23, ADR-0020).

    Branch não é presa a usuário: qualquer editor do experimento commita
    nela. O fork materializa as árvores de gates da branch base — o estado
    visível é sempre linhas reais, nunca replay. `fork_snapshot` guarda o
    mapa do fork para o merge: {"pairs": {<gate_id_da_branch>:
    <gate_id_da_base>}, "base": {<gate_id_da_base>: {campos no fork}}}.

    `is_main` marca a linha vigente do experimento (criada por
    `ensure_main_branch`); `active=False` arquiva a branch (soft delete,
    ADR-0005) — a main nunca é arquivada.
    """

    class Meta:
        db_table = "analysis_branch"
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "name"],
                condition=models.Q(active=True),
                name="unique_branch_name_per_experiment",
            )
        ]
        indexes = [models.Index(fields=["experiment"])]

    experiment = models.ForeignKey(
        ExperimentModel,
        on_delete=models.CASCADE,
        related_name="analysis_branches",
    )
    name = models.CharField(max_length=50)
    base_branch = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="derived_branches",
    )
    is_main = models.BooleanField(default=False)
    fork_snapshot = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analysis_branches_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Branch {self.id} – {self.name} (exp {self.experiment_id})"


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
                fields=["name", "file_data", "branch"],
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
    # BE-23: toda gate pertence a uma branch (auto-atribuída à main no
    # save quando omitida). `copied_from` continua marcando a família de
    # cópias intra-branch (ADR-0003); a linhagem cross-branch usa
    # `forked_from`, que NUNCA é desfeita por edições de geometria.
    branch = models.ForeignKey(
        AnalysisBranch,
        on_delete=models.CASCADE,
        related_name="gates",
    )
    forked_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="forked_copies",
    )
    color = models.CharField(max_length=7, null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="gates_created",
    )
    # BE-27/FE-42: True quando o gate foi gerado por automação (Juvia) em
    # vez do analista — a UI diferencia sugestão de população humana.
    automatic = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"Gate {self.id} – {self.name}"

    def save(self, *args, **kwargs):
        # Gates criadas sem branch explícita caem na main do experimento —
        # mantém compatível todo caminho que não conhece branches.
        if self.branch_id is None and self.file_data_id is not None:
            from analytics.services.branches import ensure_main_branch

            self.branch = ensure_main_branch(self.file_data.experiment)
        super().save(*args, **kwargs)

    @classmethod
    def build_tree(cls, file_data_id, branch=None):
        """
        Constrói uma estrutura de árvore de gates a partir de dados de um arquivo.

        `branch` (id ou AnalysisBranch) seleciona a linha de análise —
        default: a main do experimento da amostra.
        """
        from analytics.models import AnalysisResult

        branch_id = getattr(branch, "id", branch)
        if branch_id is None:
            from analytics.services.branches import ensure_main_branch

            fd = FileDataModel.objects.only("experiment_id").get(pk=file_data_id)
            branch_id = ensure_main_branch(fd.experiment).id

        gates = list(
            cls.objects.filter(file_data_id=file_data_id, branch_id=branch_id).values(
                "id",
                "name",
                "parent_id",
                "gate_coordinates",
                "plot_config",
                "copied_from_id",
                "forked_from_id",
                "branch_id",
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
    TARGET_BRANCH = "branch"
    TARGET_CHOICES = [
        (TARGET_GATE, "Gate"),
        (TARGET_SUBSAMPLE, "Subsample"),
        (TARGET_FILE, "Amostra"),
        (TARGET_EXPERIMENT, "Experimento"),
        (TARGET_COMPENSATION, "Compensação"),
        (TARGET_BRANCH, "Branch"),
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
    ACTION_FORK = "fork"
    ACTION_MERGE = "merge"
    ACTION_TAGS = "tags"
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
        (ACTION_FORK, "Criação de branch"),
        (ACTION_MERGE, "Merge de branch"),
        (ACTION_TAGS, "Tags de amostra"),
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
    # BE-23: a linha de análise que a revisão pertence. NULL = ação
    # experiment-wide (move_subsample, compensação) — aparece em qualquer
    # recorte `?branch=` da timeline, como `file_data` NULL no `?file=`.
    branch = models.ForeignKey(
        AnalysisBranch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="revisions",
    )
    # BE-27: procedência da mutação. NULL/"user" = ação do analista;
    # "juvia" = materializada por serviço — o front renderiza a origem na
    # timeline (FE-42, sinal de supervisão do juvia ADR-0003).
    ORIGIN_USER = "user"
    ORIGIN_JUVIA = "juvia"
    ORIGIN_CHOICES = [
        (ORIGIN_USER, "Usuário"),
        (ORIGIN_JUVIA, "Juvia"),
    ]
    origin = models.CharField(
        max_length=20,
        choices=ORIGIN_CHOICES,
        default=ORIGIN_USER,
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
    # BE-27: procedência do marco — "juvia" identifica checkpoints criados
    # pela materialização de um job, para a timeline diferenciar.
    origin = models.CharField(
        max_length=20,
        choices=AnalysisRevision.ORIGIN_CHOICES,
        default=AnalysisRevision.ORIGIN_USER,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analysis_checkpoints",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        tag = self.message or f"#{self.revision_id or 0}"
        return f"Checkpoint {self.id} – {tag}"


class AnalysisFigure(models.Model):
    """Figura de análise persistida (BE-33) — artefato de relatório.

    Guarda a *receita* (``spec``: grupos de réplicas, populações por
    caminho de nomes, métrica, canal) e um ``result_cache`` regenerável —
    mesma filosofia do Parquet (L2, ADR-0004): o produto pronto é
    derivado; a procedência é o ``result_revision`` (a revisão head do
    experimento no momento do cômputo). ``is_stale`` é calculado na
    leitura por fingerprint: só revisões que tocam os alvos resolvidos
    (ou ações experiment-wide) marcam a figura.

    ``published`` trava a figura para relatório: PATCH de spec e
    ``recompute/`` retornam 409 até despublicar. ``active=False`` é o
    descarte (soft delete, ADR-0005). CRUD de figura não gera
    ``AnalysisRevision`` — a timeline é da estratégia de análise.

    ``branch`` existe no modelo (custo zero — a tabela já existe), mas o
    v1 ignora: a figura é do experimento e resolve populações na main.
    """

    CHART_STATS_BAR = "stats_bar"
    CHART_STATS_STRIP = "stats_strip"
    CHART_DISTRIBUTION = "distribution"
    CHART_TYPE_CHOICES = [
        (CHART_STATS_BAR, "Barras agrupadas"),
        (CHART_STATS_STRIP, "Pontos individuais"),
        (CHART_DISTRIBUTION, "Distribuição sobreposta"),
    ]

    class Meta:
        db_table = "analysis_figure"
        indexes = [models.Index(fields=["experiment", "-updated_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "name"],
                condition=models.Q(active=True),
                name="unique_figure_name_per_experiment",
            )
        ]

    experiment = models.ForeignKey(
        ExperimentModel,
        on_delete=models.CASCADE,
        related_name="analysis_figures",
    )
    branch = models.ForeignKey(
        AnalysisBranch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analysis_figures",
    )
    name = models.CharField(max_length=120)
    chart_type = models.CharField(max_length=20, choices=CHART_TYPE_CHOICES)
    spec = models.JSONField(default=dict)
    result_cache = models.JSONField(null=True, blank=True)
    result_revision = models.ForeignKey(
        AnalysisRevision,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analysis_figures",
    )
    published = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analysis_figures_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    active = models.BooleanField(default=True, db_index=True)

    def __str__(self) -> str:
        return f"Figure {self.id} – {self.name} (exp {self.experiment_id})"


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
    # BE-35: proveniência do ajuste manual — "esta matriz é um ajuste
    # daquela". Os valores são imutáveis: editar uma matriz cria uma nova
    # derivada, e a cadeia calculada → ajustada → re-ajustada fica no banco.
    derived_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="adjustments",
    )
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
        tag = self.name or self.get_source_display()
        return f"Compensation {self.id} – {tag}"


class AnalysisJob(models.Model):
    """Job assíncrono de análise consumido por worker interno (BE-27).

    A fila mora no Postgres do Pandora (juvia ADR-0002): o front enfileira
    (`pending`), o Juvia faz claim atômico (`processing`, FOR UPDATE SKIP
    LOCKED em `/internal/jobs/claim`), executa e responde `complete` ou
    `fail`. `error` = falha ainda elegível a retentativa (o próximo claim
    a reenfileira enquanto `attempts < max_attempts`); `quarantine` é
    estado terminal para revisão manual — job nunca é descartado nem
    reexecutado sozinho depois disso.

    Um claim em `processing` sem `finished_at` expira depois de
    `JUVIA_JOB_CLAIM_TIMEOUT_MINUTES` e volta a ser elegível — worker que
    morreu depois do claim não trava a fila. `result` guarda o payload
    que o worker postou + os artefatos materializados (gates, revisão,
    checkpoint) para polling e re-complete idempotente.
    """

    class Meta:
        db_table = "analysis_job"
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["experiment", "-created_at"]),
        ]

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_DONE = "done"
    STATUS_ERROR = "error"
    STATUS_QUARANTINE = "quarantine"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Na fila"),
        (STATUS_PROCESSING, "Processando"),
        (STATUS_DONE, "Concluído"),
        (STATUS_ERROR, "Erro (retentando)"),
        (STATUS_QUARANTINE, "Quarentena"),
    ]

    # O que o worker executa. v1 = clusterização (Juvia `POST /cluster`
    # alimentado pelos eventos de `/internal/files/{id}/events`).
    OPERATION_CLUSTER = "cluster"
    OPERATION_CHOICES = [
        (OPERATION_CLUSTER, "Clusterização"),
    ]

    experiment = models.ForeignKey(
        ExperimentModel,
        on_delete=models.CASCADE,
        related_name="analysis_jobs",
    )
    file_data = models.ForeignKey(
        FileDataModel,
        on_delete=models.CASCADE,
        related_name="analysis_jobs",
    )
    # Contexto opcional do job: subsample e gate pai (população de origem
    # que o worker clusteriza). SET_NULL preserva o job se o contexto for
    # removido — o complete/fail ainda consegue registrar o desfecho.
    subsample = models.ForeignKey(
        SubsampleModel,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analysis_jobs",
    )
    gate = models.ForeignKey(
        GateModel,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analysis_jobs",
    )
    operation = models.CharField(
        max_length=50,
        choices=OPERATION_CHOICES,
        default=OPERATION_CLUSTER,
    )
    # Contrato com o worker (espelha o ClusterRequest do Juvia):
    # `model` + `params` (hiperparâmetros), `transform` e `channels` (nomes
    # crus dos canais — o endpoint de eventos devolve colunas
    # normalizadas). `payload` carrega contexto extra de produto
    # (intenção da sugestão, população rara, refs de controle) sem exigir
    # migration a cada ideia nova.
    model = models.CharField(max_length=50)
    params = models.JSONField(default=dict, blank=True)
    transform = models.CharField(max_length=20, default="linear")
    channels = models.JSONField(default=list)
    payload = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    last_error = models.TextField(blank=True)
    # Resultado postado pelo worker + artefatos materializados
    # (`created_gate_ids`, `revision_id`, `checkpoint_id`).
    result = models.JSONField(null=True, blank=True)
    # Identidade do worker que fez o claim (observabilidade/debug —
    # não é token de posse; o contrato de estado já impede double-finish).
    claimed_by = models.CharField(max_length=128, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analysis_jobs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"AnalysisJob {self.id} – {self.operation}/{self.model} [{self.status}]"
