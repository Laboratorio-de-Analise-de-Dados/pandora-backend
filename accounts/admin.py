from django.contrib import admin
from .models import User, Organization, Membership, Role, Invite


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("username", "email", "is_staff", "is_superuser")


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "org_type", "external_id")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "status", "joined_at")
    list_filter = ("status", "role")


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    list_display = ("email", "organization", "role", "status", "created_at")
    list_filter = ("status",)
