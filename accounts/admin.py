# accounts/admin.py
from django.contrib import admin
from .models import User, Organization

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("username", "email", "organization", "role")

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "org_type", "external_id")