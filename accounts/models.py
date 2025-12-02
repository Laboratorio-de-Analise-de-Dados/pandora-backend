# accounts/models.py
from django.contrib.auth.models import AbstractUser
from django.db import models

class Organization(models.Model):
    name = models.CharField(max_length=255)
    org_type = models.CharField(
        max_length=50,
        choices=[("lab", "Laboratório"), ("customer", "Cliente")],
        default="customer"
    )
    external_id = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"{self.name} ({self.org_type})"

class User(AbstractUser):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users"
    )
    role = models.CharField(max_length=50, default="member")