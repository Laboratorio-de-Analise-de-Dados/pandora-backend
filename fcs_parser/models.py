from __future__ import annotations

import logging
import os

import pandas as pd
from django.conf import settings
from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.utils import timezone
from accounts.models import Organization, User

logger = logging.getLogger(__name__)


def parquet_storage_dir() -> str:
    """Pasta (fora do banco e do git) onde os Parquet de cache vivem."""
    return os.path.join(settings.MEDIA_ROOT, "parquet")


class ExperimentTypeModel(models.Model):
    """Vocabulário controlado de tipos de experimento (ADR-0023, BE-28).

    Qualquer usuário autenticado pode criar um tipo novo — basta salvar um
    experimento com um nome que ainda não existe. A unicidade é
    case-insensitive via ``name_normalized``: "Stem Cell" e "stem cell"
    convergem para a mesma entrada, e o experimento passa a guardar o
    casing canônico (``name``) no campo ``type``.
    """

    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=100)
    name_normalized = models.CharField(max_length=100, unique=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_experiment_types",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    @staticmethod
    def normalize(name: str) -> str:
        return " ".join(name.split()).lower()

    @classmethod
    def resolve(cls, name: str, user=None) -> "ExperimentTypeModel | None":
        """Retorna o tipo canônico para ``name``, criando se não existir."""
        normalized = cls.normalize(name or "")
        if not normalized:
            return None
        obj, _ = cls.objects.get_or_create(
            name_normalized=normalized,
            defaults={"name": " ".join(name.split()), "created_by": user},
        )
        return obj

    def __str__(self) -> str:
        return self.name


class ExperimentModel(models.Model):
    STATUS_CHOICES = [
        ("new", "New"),
        ("uploading", "Uploading"),
        ("processing", "Processing"),
        ("done", "Done"),
        ("error", "Error"),
    ]
    FILE_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("uploading", "Uploading"),
        ("uploaded", "Uploaded"),
        ("failed", "Failed"),
    ]

    id = models.BigAutoField(primary_key=True)
    title = models.CharField(max_length=50)
    # `type` continua sendo o label exposto na API (string); a identidade
    # canônica vive em `experiment_type` — o save() sincroniza os dois e
    # normaliza o casing para o nome do vocabulário (BE-28).
    type = models.CharField(max_length=100, null=True)
    experiment_type = models.ForeignKey(
        ExperimentTypeModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="experiments",
    )
    # Contexto livre do experimento (objetivo, painel, notas) — opcional,
    # preenchido/alterado pelo usuário (BE-24).
    description = models.TextField(blank=True, default="")
    values = ArrayField(models.TextField(), blank=True, default=list)
    active = models.BooleanField(default=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default="new")
    file_status = models.CharField(
        max_length=50, choices=FILE_STATUS_CHOICES, default="pending"
    )
    total_chunks = models.IntegerField(null=True, blank=True)
    received_chunks = ArrayField(models.IntegerField(), default=list, blank=True)
    error_info = models.JSONField(blank=True, default=dict)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="experiments", null=True
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_experiments",
    )
    zip_path = models.CharField(max_length=512, null=True, blank=True)

    class Meta:
        constraints = [
            # Evita que o MESMO usuário crie experimentos com títulos iguais na MESMA organização
            models.UniqueConstraint(
                fields=["title", "created_by", "organization"],
                name="unique_title_per_user_and_org",
                condition=models.Q(organization__isnull=False, active=True),
            ),
            # Evita que o MESMO usuário crie experimentos pessoais com títulos iguais
            models.UniqueConstraint(
                fields=["title", "created_by"],
                name="unique_title_per_user_personal",
                condition=models.Q(organization__isnull=True, active=True),
            ),
        ]

    @property
    def is_personal(self) -> bool:
        return self.organization_id is None

    def save(self, *args, **kwargs):
        # Mantém `experiment_type` (FK canônica) e `type` (label) em sincronia
        # em qualquer caminho de escrita — API, services, shell, admin.
        resolved = None
        if self.type:
            resolved = ExperimentTypeModel.resolve(self.type, self.created_by)
        if resolved is not None:
            self.experiment_type = resolved
            self.type = resolved.name
        else:
            self.experiment_type = None
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = list(
                set(update_fields) | {"type", "experiment_type"}
            )
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Experiment {self.id} – {self.title} ({self.status})"


class FileModel(models.Model):
    """Um upload de arquivo pertencente a um experimento.

    Um experimento pode receber vários uploads ao longo do tempo (FK) —
    cada FileModel é um blob (sempre ZIP; `.fcs` solto é aglutinado num
    ZIP no complete). Copiar um experimento cria outra linha apontando pro
    mesmo ``file`` sem duplicar bytes; ``sha256`` é a identidade do blob.
    """

    class Meta:
        db_table = "experiment_files"

    id = models.BigAutoField(primary_key=True)
    file_name = models.CharField(max_length=256, null=True)
    file = models.FileField(upload_to="", null=True)
    # SHA-256 do blob físico; nulo em uploads antigos (backfill incremental).
    sha256 = models.CharField(max_length=64, null=True, blank=True, db_index=True)
    experiment = models.ForeignKey(
        ExperimentModel, on_delete=models.CASCADE, related_name="uploads"
    )
    # Controle do upload em chunks deste arquivo (fluxo "adicionar arquivos").
    total_chunks = models.IntegerField(null=True, blank=True)
    received_chunks = ArrayField(models.IntegerField(), default=list, blank=True)

    def get_file_url(self):
        return settings.MEDIA_URL + str(self.file)

    def __str__(self) -> str:
        return f"File {self.id} – {self.file_name}"


class SubsampleModel(models.Model):
    """Agrupamento de amostras dentro de um experimento.

    A engine cria um subsample por diretório encontrado dentro do ZIP
    (``tempo_1/``, ``tempo_2/`` ...), mas o vínculo amostra ↔ subsample é
    editável na UI: o cliente pode renomear o subsample e mover amostras
    entre eles sem que a extração reescreva a escolha dele.
    """

    id = models.BigAutoField(primary_key=True)
    experiment = models.ForeignKey(
        ExperimentModel, on_delete=models.CASCADE, related_name="subsamples"
    )
    name = models.CharField(max_length=256)
    # Diretório relativo dentro do ZIP que originou o subsample.
    # Vazio quando o subsample foi criado pelo usuário na UI.
    source_path = models.CharField(max_length=512, blank=True, default="")
    # BE-22/ADR-0019: papel de controle de compensação. "unstained" =
    # controle negativo; "single_stain" = controle do canal em
    # `control_channel`. Arquivos dentro são réplicas (pool no cálculo).
    CONTROL_UNSTAINED = "unstained"
    CONTROL_SINGLE_STAIN = "single_stain"
    CONTROL_TYPE_CHOICES = [
        (CONTROL_UNSTAINED, "Negativo (unstained)"),
        (CONTROL_SINGLE_STAIN, "Single stain"),
    ]
    control_type = models.CharField(
        max_length=20, choices=CONTROL_TYPE_CHOICES, null=True, blank=True
    )
    control_channel = models.CharField(max_length=256, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_subsamples",
    )
    # Nada é deletado: o subsample é inativado e suas amostras voltam para
    # "sem subsample".
    active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "subsamples"
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "name"],
                name="unique_subsample_name_per_experiment",
            )
        ]

    def __str__(self) -> str:
        return f"Subsample {self.id} – {self.name}"


class FileDataModel(models.Model):
    """Model for Data on each file"""

    id = models.BigAutoField(primary_key=True)
    file_name = models.CharField(max_length=256, null=True)
    # Caminho relativo dentro do ZIP (ex.: "tempo_1/a1.fcs"). É a identidade
    # real da amostra: dois arquivos podem ter o mesmo `file_name` em pastas
    # diferentes.
    source_path = models.CharField(max_length=512, blank=True, default="")
    # Identidade lógica do .fcs (keyword `guid` do header). Âncora de
    # cópia/rollback; `source_path` fica como dica de agrupamento inicial.
    content_guid = models.CharField(
        max_length=256, null=True, blank=True, db_index=True
    )
    # SHA-256 do .fcs individual — dedup por amostra, não pelo blob (ZIP)
    # inteiro. Permite detectar o mesmo arquivo em uploads/ZIPs diferentes.
    content_sha256 = models.CharField(
        max_length=64, null=True, blank=True, db_index=True
    )
    subsample = models.ForeignKey(
        "SubsampleModel",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="files",
    )
    experiment = models.ForeignKey(ExperimentModel, on_delete=models.CASCADE)
    headers = models.JSONField()
    # Legacy: data_set JSON in the DB. New rows use Parquet on disk.
    data_set = models.JSONField(null=True, blank=True)
    # Parquet cache path (warm cache, regenerable from the ZIP).
    parquet_path = models.CharField(max_length=512, null=True, blank=True)
    # Legacy: direct .fcs path (kept for backward compatibility with old data).
    fcs_path = models.CharField(max_length=512, null=True, blank=True)
    # Last access timestamp; used by cold-Parquet cleanup.
    last_accessed = models.DateTimeField(null=True, blank=True)
    file = models.ForeignKey(
        FileModel, on_delete=models.CASCADE, related_name="extracted_data"
    )
    # Freezer: amostra desabilitada sai das listagens e das análises, mas os
    # dados (Parquet/ZIP) e os gates continuam intactos e podem ser reativados.
    # A limpeza física de arquivos inativos e frios depende de uma rotina de
    # retenção ainda não implementada — sem ela o disco cresce indefinidamente.
    active = models.BooleanField(default=True, db_index=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deactivated_files",
    )
    # BE-34: tags semânticas por amostra (chips). Tipos de controle são
    # tags de sistema; tags de usuário são vocabulário por escopo.
    tags = models.ManyToManyField(
        "SampleTagModel",
        through="FileTagModel",
        related_name="tagged_files",
        blank=True,
    )

    class Meta:
        db_table = "file_data"
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "source_path"],
                condition=~models.Q(source_path=""),
                name="unique_source_path_per_experiment",
            ),
            models.UniqueConstraint(
                fields=["experiment", "content_guid"],
                condition=~models.Q(content_guid__isnull=True)
                & ~models.Q(content_guid=""),
                name="unique_content_guid_per_experiment",
            ),
        ]

    def __str__(self) -> str:
        return f"FileData {self.id} – {self.file_name}"

    def _touch(self):
        """Marca o ultimo acesso sem disparar um save completo."""
        now = timezone.now()
        self.last_accessed = now
        if self.pk:
            FileDataModel.objects.filter(pk=self.pk).update(last_accessed=now)

    def save_dataframe(self, df: pd.DataFrame):
        """Grava o dataset em Parquet (disco), guarda o caminho e descarta o JSON."""
        os.makedirs(parquet_storage_dir(), exist_ok=True)
        path = os.path.join(parquet_storage_dir(), f"{self.pk}.parquet")
        df.to_parquet(path, index=False)
        self.parquet_path = path
        self.data_set = None
        self.save(update_fields=["parquet_path", "data_set"])

    def get_dataframe(self) -> pd.DataFrame:
        """Return events as a DataFrame, rebuilding the cache when needed.

        Cascade: Parquet (L2 cache) -> re-extract from ZIP (L0 source) -> data_set (legacy).
        """
        # 1) Warm cache: Parquet on disk.
        if self.parquet_path and os.path.exists(self.parquet_path):
            try:
                df = pd.read_parquet(self.parquet_path)
                self._touch()
                return df
            except Exception:
                pass

        # 2) Source of truth: extract .fcs from ZIP, reparse, rebuild Parquet.
        df = self._rebuild_from_zip()
        if df is not None:
            return df

        # 3) Legacy: old fcs_path on disk (pre-ZIP migration data).
        if self.fcs_path and os.path.exists(self.fcs_path):
            df = self._rebuild_from_fcs_path()
            if df is not None:
                return df

        # 4) Legacy fallback: data_set JSON still in the DB.
        if self.data_set:
            return pd.DataFrame(self.data_set)

        return pd.DataFrame()

    def _rebuild_from_zip(self) -> pd.DataFrame | None:
        """Extract .fcs from this sample's own upload ZIP and rebuild cache."""
        from fcs_parser.services.process_experiment_file import extract_fcs_from_zip
        from fcs_parser.services.process_fcs import process_fcs_file

        upload = self.file
        if upload is None:
            return None

        fcs_path = extract_fcs_from_zip(upload, self.source_path or self.file_name)
        if fcs_path is None:
            return None

        try:
            result = process_fcs_file(fcs_path)
            df = pd.DataFrame(result.data)
            try:
                self.save_dataframe(df)
                self._touch()
            except Exception:
                logger.warning("Failed to save Parquet cache for FileData %s.", self.pk)
            return df
        except ValueError:
            logger.warning("Failed to reparse .fcs for FileData %s.", self.pk)
            return None
        finally:
            if fcs_path and os.path.exists(fcs_path):
                os.remove(fcs_path)

    def _rebuild_from_fcs_path(self) -> pd.DataFrame | None:
        """Legacy fallback: rebuild Parquet from a direct .fcs path on disk."""
        from fcs_parser.services.process_fcs import process_fcs_file

        try:
            result = process_fcs_file(self.fcs_path)
            df = pd.DataFrame(result.data)
            try:
                self.save_dataframe(df)
                self._touch()
            except Exception:
                logger.warning(
                    "Failed to save Parquet cache for FileData %s (fcs_path).",
                    self.pk,
                )
            return df
        except ValueError:
            logger.warning(
                "Failed to reparse .fcs at '%s' for FileData %s.",
                self.fcs_path,
                self.pk,
            )
            return None


class SampleTagModel(models.Model):
    """Vocabulário de tags de amostra (BE-34).

    Tags de ``scope="system"`` são seeded por migration e carregam a
    semântica que o código consome — o código referencia ``system_key``,
    nunca ``name``. Tags de usuário são vocabulário extensível por
    escopo (organização ou pessoal), mesmo padrão do BE-25/28: texto
    livre fragmenta, enum congela.

    ``category="control"`` tem regra de exclusividade por amostra — uma
    amostra não é FMO e unstained ao mesmo tempo. A regra não cabe em
    constraint SQL (a categoria mora na tag), então toda escrita passa
    por ``fcs_parser.services.tags.set_file_tags``.
    """

    SCOPE_SYSTEM = "system"
    SCOPE_ORGANIZATION = "organization"
    SCOPE_PERSONAL = "personal"
    SCOPE_CHOICES = [
        (SCOPE_SYSTEM, "Sistema"),
        (SCOPE_ORGANIZATION, "Organização"),
        (SCOPE_PERSONAL, "Pessoal"),
    ]

    CATEGORY_CONTROL = "control"
    CATEGORY_GENERAL = "general"
    CATEGORY_CHOICES = [
        (CATEGORY_CONTROL, "Controle"),
        (CATEGORY_GENERAL, "Geral"),
    ]

    id = models.BigAutoField(primary_key=True)
    # `name` é o casing canônico; o dedup é por `name_normalized` dentro
    # do escopo (ver `scope_key`).
    name = models.CharField(max_length=100)
    name_normalized = models.CharField(max_length=100)
    # Chave estável referenciada pelo código (ex.: "fmo", "unstained").
    # Só tags de sistema têm — null em tags de usuário.
    system_key = models.CharField(max_length=50, unique=True, null=True, blank=True)
    category = models.CharField(
        max_length=20, choices=CATEGORY_CHOICES, default=CATEGORY_GENERAL
    )
    color = models.CharField(max_length=7, default="#6b7280")
    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES)
    organization = models.ForeignKey(
        Organization,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="sample_tags",
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_sample_tags",
    )
    # Chave de unicidade por escopo, preenchida no save: "system",
    # "org:<id>" ou "user:<id>". Resolve o problema de NULLs distintos
    # no UniqueConstraint (Postgres não deduplica NULL).
    scope_key = models.CharField(max_length=64)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sample_tags"
        constraints = [
            models.UniqueConstraint(
                fields=["scope_key", "name_normalized"],
                name="unique_tag_name_per_scope",
            )
        ]

    @staticmethod
    def normalize(name: str) -> str:
        return " ".join(name.split()).lower()

    def save(self, *args, **kwargs):
        self.name_normalized = self.normalize(self.name)
        if self.scope == self.SCOPE_SYSTEM:
            self.scope_key = "system"
        elif self.scope == self.SCOPE_ORGANIZATION:
            self.scope_key = f"org:{self.organization_id}"
        else:
            self.scope_key = f"user:{self.created_by_id}"
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class FileTagModel(models.Model):
    """Vínculo amostra ↔ tag (through explícito de `FileData.tags`).

    Escrita exclusiva via ``set_file_tags`` — é lá que a regra "uma
    tag de controle por amostra" é validada.
    """

    id = models.BigAutoField(primary_key=True)
    file_data = models.ForeignKey(
        FileDataModel, on_delete=models.CASCADE, related_name="file_tags"
    )
    tag = models.ForeignKey(
        SampleTagModel, on_delete=models.CASCADE, related_name="file_links"
    )
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_file_tags",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "file_tags"
        constraints = [
            models.UniqueConstraint(
                fields=["file_data", "tag"], name="unique_tag_per_file"
            )
        ]
