from django.conf import settings
from django.db import models
from django.contrib.postgres.fields import ArrayField
from accounts.models import Organization, User


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


