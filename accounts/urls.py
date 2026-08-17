from django.urls import path
from .views import (
    AuthProvidersConfigView,
    CustomTokenObtainPairView,
    GoogleAuthCallbackView,
    GoogleAuthInitView,
    InviteAcceptView,
    InviteDeclineView,
    InviteListCreateView,
    InvitePublicDetailView,
    InviteRetrieveUpdateDestroyView,
    MyPendingInvitesView,
    MembershipListCreateView,
    MembershipRetrieveUpdateDestroyView,
    MicrosoftAuthCallbackView,
    MicrosoftAuthInitView,
    OrganizationListCreateView,
    OrganizationRetrieveUpdateDestroyView,
    PasswordUpdateView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegisterView,
    RetrieveUserView,
    RoleListCreateView,
    RoleRetrieveUpdateDestroyView,
    UserListCreateView,
    UserMembershipListView,
    UserRetrieveUpdateDestroyView,
)
from rest_framework_simplejwt.views import TokenRefreshView


urlpatterns = [
    path(
        "organizations/",
        OrganizationListCreateView.as_view(),
        name="organization_list_create",
    ),
    path(
        "organizations/<int:pk>/",
        OrganizationRetrieveUpdateDestroyView.as_view(),
        name="organization_detail",
    ),
    path(
        "organizations/<int:organization_id>/memberships/",
        MembershipListCreateView.as_view(),
        name="membership_list_create",
    ),
    path(
        "organizations/<int:organization_id>/memberships/<int:pk>/",
        MembershipRetrieveUpdateDestroyView.as_view(),
        name="membership_detail",
    ),
    path(
        "organizations/<int:organization_id>/invites/",
        InviteListCreateView.as_view(),
        name="invite_list_create",
    ),
    path(
        "organizations/<int:organization_id>/invites/<int:pk>/",
        InviteRetrieveUpdateDestroyView.as_view(),
        name="invite_detail",
    ),
    path(
        "invites/pending/",
        MyPendingInvitesView.as_view(),
        name="my_pending_invites",
    ),
    path(
        "invites/<str:token>/",
        InvitePublicDetailView.as_view(),
        name="invite_public_detail",
    ),
    path(
        "invites/accept/<str:token>/", InviteAcceptView.as_view(), name="invite_accept"
    ),
    path(
        "invites/decline/<str:token>/", InviteDeclineView.as_view(), name="invite_decline"
    ),
    path("roles/", RoleListCreateView.as_view(), name="role_list_create"),
    path(
        "roles/<int:pk>/", RoleRetrieveUpdateDestroyView.as_view(), name="role_detail"
    ),
    path("users/", UserListCreateView.as_view(), name="user_list_create"),
    path(
        "users/<int:pk>/", UserRetrieveUpdateDestroyView.as_view(), name="user_detail"
    ),
    path("users/me/", RetrieveUserView.as_view(), name="user_me"),
    path(
        "users/me/memberships/",
        UserMembershipListView.as_view(),
        name="user_memberships",
    ),
    path(
        "users/me/password/", PasswordUpdateView.as_view(), name="user_password_update"
    ),
    path(
        "password-reset/",
        PasswordResetRequestView.as_view(),
        name="password_reset_request",
    ),
    path(
        "password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("register/", RegisterView.as_view(), name="register"),
    path(
        "auth/providers/",
        AuthProvidersConfigView.as_view(),
        name="auth_providers_config",
    ),
    path(
        "auth/microsoft/",
        MicrosoftAuthInitView.as_view(),
        name="microsoft_auth_init",
    ),
    path(
        "auth/microsoft/callback/",
        MicrosoftAuthCallbackView.as_view(),
        name="microsoft_auth_callback",
    ),
    path(
        "auth/google/",
        GoogleAuthInitView.as_view(),
        name="google_auth_init",
    ),
    path(
        "auth/google/callback/",
        GoogleAuthCallbackView.as_view(),
        name="google_auth_callback",
    ),
    path("login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]
