from typing import Any
from django.conf import settings
from django.db import models
from django.contrib.postgres.fields import ArrayField


class ExperimentModel(models.Model):
    """Model for FCS file"""

    STATUS_CHOICES = [
        ("new", "New"),
        ("processing", "Processing"),
        ("done", "Done"),
        ("error", "Error"),
    ]

    id = models.BigAutoField(primary_key=True)
    title = models.CharField(max_length=50, unique=True)
    type = models.CharField(max_length=100, null=True)
    values = ArrayField(models.TextField(), blank=True, default=list)
    active = models.BooleanField(default=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default="new")
    error_info = models.JSONField(blank=True, default=dict)

    class Meta:
        db_table = "experiment"

    def delete(
        self, using: Any = ..., keep_parents: bool = ...
    ) -> tuple[int, dict[str, int]]:
        self.active = False
        self.save()
        return


class FileModel(models.Model):
    """Model for Experiment Files"""

    class Meta:
        db_table = "experiment_files"

    id = models.BigAutoField(primary_key=True)
    file_name = models.CharField(max_length=256, null=True)
    file = models.FileField(upload_to="storage", null=True)
    experiment = models.OneToOneField(ExperimentModel, on_delete=models.CASCADE)

    def get_file_url(self):
        return settings.MEDIA_URL + str(self.file)


class FileDataModel(models.Model):
    """Model for Data on each file"""

    id = models.BigAutoField(primary_key=True)
    file_name = models.CharField(max_length=256, null=True)
    experiment = models.ForeignKey(ExperimentModel, on_delete=models.CASCADE)
    headers = models.JSONField()
    data_set = models.JSONField()
    file = models.ForeignKey(
        FileModel, on_delete=models.CASCADE, related_name="extracted_data"
    )

    class Meta:
        db_table = "file_data"

    def is_valid(self, raise_exception=False):
        headers = self.initial_data.get("headers")
        data_set = self.initial_data.get("data_set")

        if not self.validate_json_field(headers):
            self.errors["headers"] = ["Invalid field headers."]
        if not self.validate_json_field(data_set):
            self.errors["data_set"] = ["Invalid field data_set."]

        return super().is_valid(raise_exception)


class GateModel(models.Model):
    experiment = models.ForeignKey(
        ExperimentModel, related_name="gates", on_delete=models.CASCADE
    )
    file_data = models.ForeignKey(
        FileDataModel, related_name="gates", on_delete=models.CASCADE, null=True
    )
    name = models.CharField(unique=True)
    x_min = models.FloatField()
    x_max = models.FloatField()
    y_min = models.FloatField()
    y_max = models.FloatField()
    x_axis = models.CharField()
    y_axis = models.CharField()
    created_at = models.DateTimeField(auto_now_add=True)
    parent = models.ForeignKey(
        "self", related_name="children", on_delete=models.CASCADE, null=True, blank=True
    )
