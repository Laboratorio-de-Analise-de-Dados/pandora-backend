from rest_framework.permissions import BasePermission, SAFE_METHODS
from accounts.models import Membership, Role


class IsSuperAdmin(BasePermission):
    """Acesso total para superusers/staff (dev/suporte)."""

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_super_admin
        )


class IsOrgAdmin(BasePermission):
    """Admin da organização ou super_admin."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_super_admin:
            return True

        org_id = view.kwargs.get("org_id") or view.kwargs.get("organization_id")
        if not org_id:
            return False

        return Membership.objects.filter(
            user=user,
            organization_id=org_id,
            role__name=Role.ORG_ADMIN,
            status="active",
        ).exists()


class IsOrgMember(BasePermission):
    """Membro ativo da organização (qualquer role) ou super_admin."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_super_admin:
            return True

        org_id = view.kwargs.get("org_id") or view.kwargs.get("organization_id")
        if not org_id:
            return False

        return Membership.objects.filter(
            user=user,
            organization_id=org_id,
            status="active",
        ).exists()
