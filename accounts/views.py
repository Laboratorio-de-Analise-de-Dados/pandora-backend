import base64
import hashlib
import secrets
import urllib.parse
import requests
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.shortcuts import get_object_or_404, redirect
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, serializers
from accounts.permissions.has_permission import IsOrgAdmin, IsSuperAdmin
from accounts.serializers import (
    OrganizationDetailSerializer,
    OrganizationListSerializer,
    UserCreateSerializer,
    UserDetailSerializer,
    UserListSerializer,
)
from utils.mixins import SerializerByMethodMixin
from .models import Invite, Membership, Organization, Role, User
from rest_framework_simplejwt.views import TokenObtainPairView
from .serializers import (
    CustomTokenObtainPairSerializer,
    InviteAcceptSerializer,
    InviteCreateSerializer,
    InviteSerializer,
    MembershipCreateSerializer,
    MembershipSerializer,
    RoleSerializer,
    UserMembershipSerializer,
    UserRegisterSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
)
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from .serializers import get_or_create_default_roles
from accounts.services.send_mail import send_invite_email, send_password_reset_email


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class OrganizationListCreateView(SerializerByMethodMixin, generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrganizationListSerializer
    serializer_map = {
        "POST": OrganizationDetailSerializer,
    }

    def get_queryset(self):
        user = self.request.user
        if user.is_super_admin:
            return Organization.objects.all()
        return Organization.objects.filter(
            memberships__user=user, memberships__status="active"
        ).distinct()

    def perform_create(self, serializer):
        org = serializer.save()
        roles = get_or_create_default_roles()
        Membership.objects.create(
            user=self.request.user,
            organization=org,
            role=roles[Role.ORG_ADMIN],
            status="active",
        )


class OrganizationRetrieveUpdateDestroyView(
    SerializerByMethodMixin, generics.RetrieveUpdateDestroyAPIView
):
    permission_classes = [IsAuthenticated]
    queryset = Organization.objects.all()
    serializer_class = OrganizationDetailSerializer
    serializer_map = {
        "GET": OrganizationDetailSerializer,
        "PUT": OrganizationDetailSerializer,
        "PATCH": OrganizationDetailSerializer,
        "DELETE": OrganizationDetailSerializer,
    }


class UserListCreateView(SerializerByMethodMixin, generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    queryset = User.objects.all()
    serializer_class = UserListSerializer
    serializer_map = {
        "POST": UserCreateSerializer,
    }


class UserRetrieveUpdateDestroyView(
    SerializerByMethodMixin, generics.RetrieveUpdateDestroyAPIView
):
    permission_classes = [IsAuthenticated]
    queryset = User.objects.all()
    serializer_class = UserDetailSerializer
    serializer_map = {
        "GET": UserDetailSerializer,
        "PUT": UserCreateSerializer,
        "PATCH": UserCreateSerializer,
        "DELETE": UserDetailSerializer,
    }

    def get_object(self):
        pk = self.kwargs.get("pk")
        if pk == "me":
            return self.request.user
        return super().get_object()


class MembershipListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def get_queryset(self):
        org_id = self.kwargs["organization_id"]
        return Membership.objects.filter(organization_id=org_id).select_related(
            "user", "role"
        )

    def get_serializer_class(self):
        if self.request.method == "POST":
            return MembershipCreateSerializer
        return MembershipSerializer

    def perform_create(self, serializer):
        org_id = self.kwargs["organization_id"]
        serializer.save(organization_id=org_id)


class MembershipRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]
    queryset = Membership.objects.all().select_related("user", "role", "organization")
    serializer_class = MembershipSerializer

    def get_serializer_class(self):
        if self.request.method in ["PUT", "PATCH"]:
            return MembershipCreateSerializer
        return MembershipSerializer


class InviteListCreateView(SerializerByMethodMixin, generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]
    serializer_class = InviteSerializer
    serializer_map = {
        "POST": InviteCreateSerializer,
    }

    def get_queryset(self):
        org_id = self.kwargs["organization_id"]
        return Invite.objects.filter(organization_id=org_id)

    def perform_create(self, serializer):
        org_id = self.kwargs["organization_id"]
        return serializer.save(
            organization_id=org_id, role=self.request.data.get("role", Role.MEMBER)
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = self.perform_create(serializer)
        email_sent = send_invite_email(instance)
        output = InviteSerializer(instance, context={"request": request})
        return Response(
            {**output.data, "email_sent": email_sent}, status=status.HTTP_201_CREATED
        )


class InviteAcceptView(generics.GenericAPIView):
    serializer_class = InviteAcceptSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        data = {**request.data, "token": self.kwargs["token"]}
        serializer = self.get_serializer(data=data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        result = serializer.save()

        return Response(
            {
                "invite": InviteSerializer(result["invite"]).data,
                "membership": MembershipSerializer(result["membership"]).data,
            },
            status=status.HTTP_200_OK,
        )


class InviteDeclineView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        token = self.kwargs["token"]
        user = request.user
        invite = get_object_or_404(
            Invite, token=token, email__iexact=user.email, status="pending"
        )
        invite.status = "declined"
        invite.save(update_fields=["status"])
        return Response({"detail": "Convite recusado."}, status=status.HTTP_200_OK)


class InviteRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]
    queryset = Invite.objects.all()
    serializer_class = InviteSerializer

    def perform_destroy(self, instance):
        instance.status = "canceled"
        instance.save(update_fields=["status"])


class InvitePublicDetailView(generics.RetrieveAPIView):
    serializer_class = InviteSerializer
    permission_classes = []
    queryset = Invite.objects.all()
    lookup_field = "token"
    lookup_url_kwarg = "token"


class MyPendingInvitesView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = InviteSerializer

    def get_queryset(self):
        return Invite.objects.filter(
            email__iexact=self.request.user.email, status="pending"
        ).select_related("organization", "role")


class RoleListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    queryset = Role.objects.all()
    serializer_class = RoleSerializer


class RoleRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    queryset = Role.objects.all()
    serializer_class = RoleSerializer


class UserMembershipListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserMembershipSerializer

    def get_queryset(self):
        return Membership.objects.filter(user=self.request.user).select_related(
            "organization", "role"
        )


class PasswordUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=inline_serializer(
            name="PasswordUpdateRequest",
            fields={
                "current_password": serializers.CharField(),
                "new_password": serializers.CharField(),
            },
        ),
        responses=inline_serializer(
            name="DetailResponse",
            fields={"detail": serializers.CharField()},
        ),
    )
    def post(self, request):
        user = request.user
        current = request.data.get("current_password")
        new = request.data.get("new_password")

        if not user.check_password(current):
            return Response(
                {"detail": "Senha atual incorreta"}, status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new)
        user.save()
        return Response(
            {"detail": "Senha atualizada com sucesso"}, status=status.HTTP_200_OK
        )


class RetrieveUserView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserDetailSerializer

    @extend_schema(responses=UserDetailSerializer)
    def get(self, request):
        serializer = UserDetailSerializer(request.user, context={"request": request})
        return Response(serializer.data)


class RegisterView(generics.CreateAPIView):
    serializer_class = UserRegisterSerializer
    permission_classes = []

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                "id": user.id,
                "username": user.username,
                "email": user.email,
            },
            status=status.HTTP_201_CREATED,
        )


class PasswordResetRequestView(generics.GenericAPIView):
    serializer_class = PasswordResetRequestSerializer
    permission_classes = []

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        user = User.objects.filter(email__iexact=email).first()
        email_sent = False
        if user:
            token = default_token_generator.make_token(user)
            reset_link = (
                f"{settings.FRONTEND_URL}/reset-password?token={user.id}:{token}"
            )
            email_sent = send_password_reset_email(user, reset_link)

        return Response(
            {
                "detail": "Se o email existir, você receberá um link de recuperação.",
                "email_sent": email_sent,
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(generics.GenericAPIView):
    serializer_class = PasswordResetConfirmSerializer
    permission_classes = []

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {"detail": "Senha redefinida com sucesso."},
            status=status.HTTP_200_OK,
        )


class AuthProvidersConfigView(APIView):
    permission_classes = []

    def get(self, request, *args, **kwargs):
        return Response(
            {
                "google": settings.GOOGLE_AUTH_ENABLED,
                "microsoft": settings.MICROSOFT_AUTH_ENABLED,
            },
            status=status.HTTP_200_OK,
        )


class MicrosoftAuthInitView(APIView):
    permission_classes = []

    def get(self, request, *args, **kwargs):
        if not settings.MICROSOFT_AUTH_ENABLED:
            return Response(
                {"detail": "Autenticação Microsoft não configurada."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        state = secrets.token_urlsafe(32)
        request.session["microsoft_auth_state"] = state

        tenant = settings.MICROSOFT_TENANT_ID or "common"
        authorize_url = (
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
        )
        params = {
            "client_id": settings.MICROSOFT_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": settings.MICROSOFT_REDIRECT_URI,
            "response_mode": "query",
            "scope": "openid email profile User.Read",
            "state": state,
        }
        url = f"{authorize_url}?{urllib.parse.urlencode(params)}"
        return redirect(url)


class MicrosoftAuthCallbackView(APIView):
    permission_classes = []

    def get(self, request, *args, **kwargs):
        if not settings.MICROSOFT_AUTH_ENABLED:
            return Response(
                {"detail": "Autenticação Microsoft não configurada."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        code = request.GET.get("code")
        state = request.GET.get("state")
        stored_state = request.session.get("microsoft_auth_state")

        if not code or state != stored_state:
            return Response(
                {"detail": "Requisição inválida ou state mismatch."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = settings.MICROSOFT_TENANT_ID or "common"
        token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

        token_data = {
            "grant_type": "authorization_code",
            "client_id": settings.MICROSOFT_CLIENT_ID,
            "client_secret": settings.MICROSOFT_CLIENT_SECRET,
            "code": code,
            "redirect_uri": settings.MICROSOFT_REDIRECT_URI,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            token_response = requests.post(token_url, data=token_data, headers=headers)
            token_response.raise_for_status()
            tokens = token_response.json()
        except requests.RequestException as e:
            return Response(
                {"detail": f"Erro ao trocar code por token: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        access_token = tokens.get("access_token")
        id_token = tokens.get("id_token")

        # Fetch user info from Microsoft Graph
        try:
            graph_response = requests.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            graph_response.raise_for_status()
            profile = graph_response.json()
        except requests.RequestException as e:
            return Response(
                {"detail": f"Erro ao obter perfil do Microsoft: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = profile.get("mail") or profile.get("userPrincipalName")
        name = profile.get("displayName") or email.split("@")[0]

        if not email:
            return Response(
                {"detail": "Não foi possível obter o email do usuário Microsoft."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, created = User.objects.get_or_create(
            email__iexact=email,
            defaults={
                "username": email.split("@")[0],
                "email": email,
                "is_active": True,
                "auth_provider": "microsoft",
            },
        )

        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        else:
            user.auth_provider = "microsoft"
            user.save(update_fields=["auth_provider"])

        # Optionally link to an org based on email domain
        org_name = email.split("@")[-1]
        organization, _ = Organization.objects.get_or_create(
            name=org_name,
            defaults={"org_type": "lab"},
        )
        roles = get_or_create_default_roles()
        Membership.objects.get_or_create(
            user=user,
            organization=organization,
            defaults={"role": roles[Role.MEMBER], "status": "active"},
        )

        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(user)

        # Redirect to frontend with tokens
        redirect_url = (
            f"{settings.FRONTEND_URL}/auth/callback?"
            f"access={str(refresh.access_token)}&"
            f"refresh={str(refresh)}&"
            f"user_id={user.id}&"
            f"username={urllib.parse.quote(user.username)}&"
            f"email={urllib.parse.quote(user.email)}"
        )
        return redirect(redirect_url)


class GoogleAuthInitView(APIView):
    permission_classes = []

    def get(self, request, *args, **kwargs):
        if not settings.GOOGLE_AUTH_ENABLED:
            return Response(
                {"detail": "Autenticação Google não configurada."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        state = secrets.token_urlsafe(32)
        request.session["google_auth_state"] = state

        authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
        params = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "scope": "openid email profile",
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
        url = f"{authorize_url}?{urllib.parse.urlencode(params)}"
        return redirect(url)


class GoogleAuthCallbackView(APIView):
    permission_classes = []

    def get(self, request, *args, **kwargs):
        if not settings.GOOGLE_AUTH_ENABLED:
            return Response(
                {"detail": "Autenticação Google não configurada."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        code = request.GET.get("code")
        state = request.GET.get("state")
        stored_state = request.session.get("google_auth_state")

        if not code or state != stored_state:
            return Response(
                {"detail": "Requisição inválida ou state mismatch."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "grant_type": "authorization_code",
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "code": code,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            token_response = requests.post(token_url, data=token_data, headers=headers)
            token_response.raise_for_status()
            tokens = token_response.json()
        except requests.RequestException as e:
            return Response(
                {"detail": f"Erro ao trocar code por token: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        access_token = tokens.get("access_token")

        try:
            userinfo_response = requests.get(
                "https://www.googleapis.com/oauth2/v1/userinfo",
                params={"alt": "json"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_response.raise_for_status()
            profile = userinfo_response.json()
        except requests.RequestException as e:
            return Response(
                {"detail": f"Erro ao obter perfil do Google: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = profile.get("email")
        name = profile.get("name") or (email.split("@")[0] if email else "")

        if not email:
            return Response(
                {"detail": "Não foi possível obter o email do usuário Google."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, created = User.objects.get_or_create(
            email__iexact=email,
            defaults={
                "username": name or email.split("@")[0],
                "email": email,
                "is_active": True,
                "auth_provider": "google",
            },
        )

        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        else:
            user.auth_provider = "google"
            user.save(update_fields=["auth_provider"])

        # Optionally link to an org based on email domain
        org_name = email.split("@")[-1]
        organization, _ = Organization.objects.get_or_create(
            name=org_name,
            defaults={"org_type": "lab"},
        )
        roles = get_or_create_default_roles()
        Membership.objects.get_or_create(
            user=user,
            organization=organization,
            defaults={"role": roles[Role.MEMBER], "status": "active"},
        )

        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(user)

        redirect_url = (
            f"{settings.FRONTEND_URL}/auth/callback?"
            f"access={str(refresh.access_token)}&"
            f"refresh={str(refresh)}&"
            f"user_id={user.id}&"
            f"username={urllib.parse.quote(user.username)}&"
            f"email={urllib.parse.quote(user.email)}"
        )
        return redirect(redirect_url)
