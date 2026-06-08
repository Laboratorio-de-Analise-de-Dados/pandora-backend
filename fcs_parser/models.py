import os

import pandas as pd
from django.conf import settings
from django.db import models
from django.contrib.postgres.fields import ArrayField
from django.utils import timezone
from accounts.models import Organization, User


def parquet_storage_dir() -> str:
    """Pasta (fora do banco e do git) onde os Parquet de cache vivem."""
    return os.path.join(settings.MEDIA_ROOT, "parquet")


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
    title = models.CharField(max_length=50, unique=True)
    type = models.CharField(max_length=100, null=True)
    values = ArrayField(models.TextField(), blank=True, default=list)
    active = models.BooleanField(default=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default="new")
    file_status = models.CharField(max_length=50, choices=FILE_STATUS_CHOICES, default="pending")
    total_chunks = models.IntegerField(null=True, blank=True)
    received_chunks = ArrayField(models.IntegerField(), default=list, blank=True)
    error_info = models.JSONField(blank=True, default=dict)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="experiments", null=True
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_experiments"
    )


class FileModel(models.Model):
    """Model for Experiment Files"""

    class Meta:
        db_table = "experiment_files"

    id = models.BigAutoField(primary_key=True)
    file_name = models.CharField(max_length=256, null=True)
    file = models.FileField(upload_to="", null=True)
    experiment = models.OneToOneField(ExperimentModel, on_delete=models.CASCADE)

    def get_file_url(self):
        return settings.MEDIA_URL + str(self.file)


class FileDataModel(models.Model):
    """Model for Data on each file"""

    id = models.BigAutoField(primary_key=True)
    file_name = models.CharField(max_length=256, null=True)
    experiment = models.ForeignKey(ExperimentModel, on_delete=models.CASCADE)
    headers = models.JSONField()
    # data_set (JSON no banco) vira legado: novas linhas guardam o parseado em
    # Parquet (parquet_path) e regeneram a partir do .fcs original (fcs_path).
    data_set = models.JSONField(null=True, blank=True)
    # Caminho do Parquet com o dataset parseado (cache morno, recriavel).
    parquet_path = models.CharField(max_length=512, null=True, blank=True)
    # Caminho do .fcs original (fonte da verdade, usado para reprocessar).
    fcs_path = models.CharField(max_length=512, null=True, blank=True)
    # Ultimo acesso ao dataset; usado pela limpeza de Parquet frio.
    last_accessed = models.DateTimeField(null=True, blank=True)
    file = models.ForeignKey(
        FileModel, on_delete=models.CASCADE, related_name="extracted_data"
    )

    class Meta:
        db_table = "file_data"

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
        """Retorna os eventos como DataFrame, reconstruindo o cache se preciso.

        Cascata: Parquet (cache) -> reparse do .fcs (fonte) -> data_set (legado).
        """
        # 1) Cache morno: Parquet em disco.
        if self.parquet_path and os.path.exists(self.parquet_path):
            try:
                df = pd.read_parquet(self.parquet_path)
                self._touch()
                return df
            except Exception:
                pass

        # 2) Fonte da verdade: reparseia o .fcs original e regenera o Parquet.
        if self.fcs_path and os.path.exists(self.fcs_path):
            from fcs_parser.services import process_fcs_file

            processed = process_fcs_file(self.fcs_path)
            if isinstance(processed, list):
                df = pd.DataFrame(processed[1])
                try:
                    self.save_dataframe(df)
                    self._touch()
                except Exception:
                    pass
                return df

        # 3) Legado: data_set JSON ainda no banco.
        if self.data_set:
            return pd.DataFrame(self.data_set)

        return pd.DataFrame()

    def is_valid(self, raise_exception=False):
        headers = self.initial_data.get("headers")
        data_set = self.initial_data.get("data_set")

        if not self.validate_json_field(headers):
            self.errors["headers"] = ["Invalid field headers."]
        if not self.validate_json_field(data_set):
            self.errors["data_set"] = ["Invalid field data_set."]

        return super().is_valid(raise_exception)


