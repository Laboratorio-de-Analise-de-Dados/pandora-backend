from django.urls import path
from .views import (
    CustomTokenObtainPairView, InviteAcceptView, InviteListCreateView, InviteRetrieveUpdateDestroyView, MembershipListCreateView, MembershipRetrieveUpdateDestroyView, OrganizationListCreateView, OrganizationRetrieveUpdateDestroyView, PasswordUpdateView, RetrieveUserView, RoleListCreateView, RoleRetrieveUpdateDestroyView, UserListCreateView, UserMembershipListView, 
    UserRetrieveUpdateDestroyView
)
from rest_framework_simplejwt.views import (
    TokenRefreshView,
)


urlpatterns = [
    path("organizations/", OrganizationListCreateView.as_view(), name="organization_list_create"),
    path("organizations/<int:pk>/", OrganizationRetrieveUpdateDestroyView.as_view(), name="organization_detail"),
    path(
        "organizations/<int:organization_id>/memberships/",
        MembershipListCreateView.as_view(),
        name="membership_list_create"
    ),
    path(
        "organizations/<int:organization_id>/memberships/<int:pk>/",
        MembershipRetrieveUpdateDestroyView.as_view(),
        name="membership_detail"
    ),
        path(
        "organizations/<int:organization_id>/invites/",
        InviteListCreateView.as_view(),
        name="invite_list_create"
    ),
    path(
        "organizations/<int:organization_id>/invites/<int:pk>/",
        InviteRetrieveUpdateDestroyView.as_view(),
        name="invite_detail"
    ),
    # rota para aceitar convite pelo token
    path(
        "invites/accept/<str:token>/",
        InviteAcceptView.as_view(),
        name="invite_accept"
    ),
    path(
        "roles/",
        RoleListCreateView.as_view(),
        name="role_list_create"
    ),
    path(
        "roles/<int:pk>/",
        RoleRetrieveUpdateDestroyView.as_view(),
        name="role_detail"
    ),
    path("users/", UserListCreateView.as_view(), name="user_list_create"),
    path("users/<int:pk>/", UserRetrieveUpdateDestroyView.as_view(), name="user_detail"),
    path("login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("users/me/", RetrieveUserView.as_view(), name="user_me"),
    path("users/me/memberships/", UserMembershipListView.as_view(), name="user_memberships"),
    path("users/me/password/", PasswordUpdateView.as_view(), name="user_password_update"),
]
