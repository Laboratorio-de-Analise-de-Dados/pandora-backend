from rest_framework.permissions import BasePermission
from accounts.models import Membership

class IsOrgAdmin(BasePermission):
    def has_permission(self, request, view):
        org_id = view.kwargs.get("org_id")
        return Membership.objects.filter(
            user=request.user,
            organization_id=org_id,
            role__name="Admin",  # ou outra regra
            status="active"
        ).exists()