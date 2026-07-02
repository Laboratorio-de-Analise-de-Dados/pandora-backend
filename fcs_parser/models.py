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

    def __str__(self) -> str:
        return f"Experiment {self.id} – {self.title} ({self.status})"


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

    def __str__(self) -> str:
        return f"File {self.id} – {self.file_name}"


class FileDataModel(models.Model):
    """Model for Data on each file"""

    id = models.BigAutoField(primary_key=True)
    file_name = models.CharField(max_length=256, null=True)
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

    class Meta:
        db_table = "file_data"

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
        """Extract .fcs from the experiment's ZIP and rebuild the Parquet cache."""
        from fcs_parser.services.process_experiment_file import extract_fcs_from_zip
        from fcs_parser.services.process_fcs import process_fcs_file

        experiment = self.experiment
        if not getattr(experiment, "zip_path", None):
            return None

        fcs_path = extract_fcs_from_zip(experiment, self.file_name)
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
