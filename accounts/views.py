import secrets
import urllib.parse
from datetime import timedelta
import requests
from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone
from django.contrib.auth.tokens import default_token_generator
from django.shortcuts import get_object_or_404, redirect
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import generics, serializers
from accounts.permissions.has_permission import IsOrgAdmin, IsSuperAdmin
from accounts.serializers import (
    OrganizationDetailSerializer,
    UserCreateSerializer,
    UserDetailSerializer,
    UserListSerializer,
)
from utils.mixins import SerializerByMethodMixin
from .models import (
    AuthEvent,
    Invite,
    Membership,
    Organization,
    Role,
    SocialAccount,
    User,
)
from rest_framework_simplejwt.views import TokenObtainPairView
from .serializers import (
    AuthEventSerializer,
    ConfirmLinkSerializer,
    CustomTokenObtainPairSerializer,
    InviteAcceptSerializer,
    InviteCreateSerializer,
    InviteSerializer,
    MembershipCreateSerializer,
    MembershipSerializer,
    RoleSerializer,
    SocialAccountSerializer,
    SocialAccountUnlinkSerializer,
    UserMembershipSerializer,
    UserRegisterSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PasswordUpdateSerializer,
)
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from .serializers import get_or_create_default_roles
from accounts.services.send_mail import send_invite_email, send_password_reset_email
from accounts.services.merge import (
    make_merge_token,
    merge_accounts,
    read_merge_token,
)
from accounts.services.oauth import (
    google_fetch_identity,
    log_auth_event,
    make_link_state,
    make_link_token,
    microsoft_fetch_identity,
    read_link_state,
    read_link_token,
    unique_username_for_email,
    unlink_social_account,
)
from django.core import signing
from rest_framework_simplejwt.tokens import RefreshToken


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            user = User.objects.filter(username=request.data.get("username")).first()
            if user:
                log_auth_event(request, user, "login_local", summary="Login com senha")
        return response


class OrganizationListCreateView(SerializerByMethodMixin, generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrganizationDetailSerializer
    serializer_map = {
        "POST": OrganizationDetailSerializer,
    }

    def get_queryset(self):
        user = self.request.user
        members = Prefetch(
            "memberships",
            queryset=Membership.objects.filter(status="active").select_related(
                "user", "role", "organization"
            ),
        )
        if user.is_super_admin:
            return Organization.objects.prefetch_related(members)
        return (
            Organization.objects.filter(
                memberships__user=user, memberships__status="active"
            )
            .prefetch_related(members)
            .distinct()
        )

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
    queryset = Organization.objects.prefetch_related(
        Prefetch(
            "memberships",
            queryset=Membership.objects.filter(status="active").select_related(
                "user", "role", "organization"
            ),
        )
    )
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


def _assert_not_last_org_admin(membership):
    """Impede que a organização fique sem nenhum admin ativo."""
    if (
        membership.role.name == Role.ORG_ADMIN
        and membership.status == "active"
        and not Membership.objects.filter(
            organization_id=membership.organization_id,
            role__name=Role.ORG_ADMIN,
            status="active",
        )
        .exclude(id=membership.id)
        .exists()
    ):
        raise serializers.ValidationError(
            {"role": "A organização precisa de pelo menos um admin ativo."}
        )


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


@extend_schema(
    description=(
        "Vínculo de um usuário com a organização. "
        "Política da API: nada é deletado fisicamente — o `DELETE` inativa "
        "o membership (`status='inactive'`), preservando o histórico."
    )
)
class MembershipRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]
    serializer_class = MembershipSerializer

    def get_queryset(self):
        return Membership.objects.filter(
            organization_id=self.kwargs["organization_id"]
        ).select_related("user", "role", "organization")

    def perform_update(self, serializer):
        membership = serializer.instance
        new_role = serializer.validated_data.get("role", membership.role)
        new_status = serializer.validated_data.get("status", membership.status)
        leaves_admin = new_role.name != Role.ORG_ADMIN or new_status != "active"
        if leaves_admin:
            _assert_not_last_org_admin(membership)
        serializer.save()

    def perform_destroy(self, instance):
        """Nunca deleta: apenas inativa o vínculo, preservando o histórico."""
        _assert_not_last_org_admin(instance)
        if instance.status != "inactive":
            instance.status = "inactive"
            instance.save(update_fields=["status"])

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


class MyOrganizationPendingInvitesView(generics.ListAPIView):
    """Convites pendentes enviados para organizações que o usuário administra."""

    permission_classes = [IsAuthenticated]
    serializer_class = InviteSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_super_admin:
            return Invite.objects.filter(status="pending").select_related(
                "organization", "role"
            )
        admin_org_ids = Membership.objects.filter(
            user=user,
            role__name=Role.ORG_ADMIN,
            status="active",
        ).values_list("organization_id", flat=True)
        return Invite.objects.filter(
            organization_id__in=admin_org_ids, status="pending"
        ).select_related("organization", "role")


class InviteResendView(generics.GenericAPIView):
    """Reenvia um convite pendente, renovando a validade."""

    permission_classes = [IsAuthenticated, IsOrgAdmin]
    serializer_class = InviteSerializer
    queryset = Invite.objects.all()

    def post(self, request, *args, **kwargs):
        org_id = self.kwargs["organization_id"]
        invite = get_object_or_404(
            Invite,
            pk=self.kwargs["pk"],
            organization_id=org_id,
            status="pending",
        )
        invite.expires_at = timezone.now() + timedelta(hours=24)
        invite.save(update_fields=["expires_at"])
        email_sent = send_invite_email(invite)
        serializer = self.get_serializer(invite, context={"request": request})
        return Response(
            {**serializer.data, "email_sent": email_sent},
            status=status.HTTP_200_OK,
        )


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
        request=PasswordUpdateSerializer,
        responses=inline_serializer(
            name="DetailResponse",
            fields={"detail": serializers.CharField()},
        ),
    )
    def post(self, request):
        user = request.user
        payload = PasswordUpdateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        if not user.check_password(payload.validated_data["current_password"]):
            return Response(
                {"detail": "Senha atual incorreta"}, status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(payload.validated_data["new_password"])
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


def _redirect_with_tokens(user):
    """Redirect padrão pós-login: emite JWT e manda o front completar."""
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


def _redirect_link_notice(provider, provider_user_id, email, user):
    """Email-match sem vínculo: o front avisa e pede confirmação (BE-29)."""
    token = make_link_token(provider, provider_user_id, email, user.id)
    params = urllib.parse.urlencode(
        {
            "link_notice": "1",
            "provider": provider,
            "email": email,
            "token": token,
        }
    )
    return redirect(f"{settings.FRONTEND_URL}/auth/callback?{params}")


def _redirect_merge_notice(provider, provider_user_id, absorbed):
    """Avisa o front que a identidade pertence a outra conta (BE-30)."""
    token = make_merge_token(provider, provider_user_id, absorbed.id)
    params = urllib.parse.urlencode(
        {
            "merge_notice": "1",
            "provider": provider,
            "email": absorbed.email,
            "token": token,
        }
    )
    return redirect(f"{settings.FRONTEND_URL}/profile?{params}")


def _link_identity_to_user(request, user, provider, provider_user_id, email):
    """Modo link (perfil): vincula a identidade ao usuário da sessão."""
    if not provider_user_id:
        return redirect(
            f"{settings.FRONTEND_URL}/profile?link_error=no_sub&provider={provider}"
        )
    existing = SocialAccount.objects.filter(
        provider=provider, provider_user_id=provider_user_id
    ).first()
    if existing and existing.user_id != user.id:
        owner = existing.user
        if owner.is_active and not owner.merged_into_id:
            # A identidade já pertence a outra conta → o front oferece
            # merge (BE-30) em vez de um erro seco.
            return _redirect_merge_notice(provider, provider_user_id, owner)
        # Conta absorvida/desativada — a identidade está livre e vai para
        # quem provou controle dela agora.
        existing.user = user
        existing.active = True
        existing.unlinked_at = None
        existing.email = email
        existing.save(update_fields=["user", "active", "unlinked_at", "email"])
    elif existing:
        if not existing.active or existing.email != email:
            existing.active = True
            existing.unlinked_at = None
            existing.email = email
            existing.save(update_fields=["active", "unlinked_at", "email"])
    else:
        SocialAccount.objects.create(
            user=user,
            provider=provider,
            provider_user_id=provider_user_id,
            email=email,
        )
    log_auth_event(
        request,
        user,
        "link",
        provider=provider,
        summary=f"Vinculou {provider} ({email})",
        provider_email=email,
    )
    # Conta legada sem SocialAccount com o mesmo email do IdP → oferece
    # merge em vez de deixar a outra conta órfã em silêncio.
    others = list(
        User.objects.filter(email__iexact=email, is_active=True).exclude(pk=user.pk)[:2]
    )
    if len(others) == 1:
        return _redirect_merge_notice(provider, provider_user_id, others[0])
    return redirect(f"{settings.FRONTEND_URL}/profile?linked={provider}")


def _complete_sso(request, provider, identity, link_user_id=None):
    """Despacha o callback OAuth: modo link, vínculo existente, aviso de
    vínculo por email-match ou criação de usuário novo."""
    sub = identity.get("sub")
    email = identity.get("email")

    if link_user_id is not None:
        user = User.objects.filter(id=link_user_id).first()
        if not user:
            return Response(
                {"detail": "Sessão de vínculo inválida."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return _link_identity_to_user(request, user, provider, sub, email)

    account = None
    if sub:
        account = SocialAccount.objects.filter(
            provider=provider, provider_user_id=sub
        ).first()
    if account and account.active:
        log_auth_event(
            request,
            account.user,
            "login_sso",
            provider=provider,
            summary=f"Login via {provider} ({email})",
            provider_email=email,
        )
        return _redirect_with_tokens(account.user)
    if account:
        # Identidade desvinculada antes — confirma para reativar no mesmo user.
        return _redirect_link_notice(provider, sub, email, account.user)

    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if user:
        return _redirect_link_notice(provider, sub, email, user)

    user = User.objects.create(
        username=unique_username_for_email(email),
        email=email,
        is_active=True,
        auth_provider=provider,
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    if sub:
        SocialAccount.objects.create(
            user=user, provider=provider, provider_user_id=sub, email=email
        )
    log_auth_event(
        request,
        user,
        "login_sso",
        provider=provider,
        summary=f"Criou conta via {provider} ({email})",
        provider_email=email,
    )
    return _redirect_with_tokens(user)


class MicrosoftAuthInitView(APIView):
    permission_classes = []
    link_mode = False

    def get(self, request, *args, **kwargs):
        if not settings.MICROSOFT_AUTH_ENABLED:
            return Response(
                {"detail": "Autenticação Microsoft não configurada."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if self.link_mode:
            # O state assinado carrega o usuário — o cookie de sessão não
            # sobreviveria ao fetch cross-origin do front.
            state = make_link_state(request.user.id)
        else:
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
        if self.link_mode:
            return Response({"authorize_url": url})
        return redirect(url)


class MicrosoftAuthLinkInitView(MicrosoftAuthInitView):
    permission_classes = [IsAuthenticated]
    link_mode = True


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
        link_payload = read_link_state(state)

        if not code or not state or (link_payload is None and state != stored_state):
            return Response(
                {"detail": "Requisição inválida ou state mismatch."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            identity = microsoft_fetch_identity(code)
        except requests.RequestException as e:
            return Response(
                {"detail": f"Erro ao obter identidade Microsoft: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not identity.get("email"):
            return Response(
                {"detail": "Não foi possível obter o email do usuário Microsoft."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        link_user_id = link_payload.get("user_id") if link_payload else None
        return _complete_sso(request, "microsoft", identity, link_user_id)


class GoogleAuthInitView(APIView):
    permission_classes = []
    link_mode = False

    def get(self, request, *args, **kwargs):
        if not settings.GOOGLE_AUTH_ENABLED:
            return Response(
                {"detail": "Autenticação Google não configurada."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if self.link_mode:
            state = make_link_state(request.user.id)
        else:
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
        if self.link_mode:
            return Response({"authorize_url": url})
        return redirect(url)


class GoogleAuthLinkInitView(GoogleAuthInitView):
    permission_classes = [IsAuthenticated]
    link_mode = True


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
        link_payload = read_link_state(state)

        if not code or not state or (link_payload is None and state != stored_state):
            return Response(
                {"detail": "Requisição inválida ou state mismatch."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            identity = google_fetch_identity(code)
        except requests.RequestException as e:
            return Response(
                {"detail": f"Erro ao obter identidade Google: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not identity.get("email"):
            return Response(
                {"detail": "Não foi possível obter o email do usuário Google."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        link_user_id = link_payload.get("user_id") if link_payload else None
        return _complete_sso(request, "google", identity, link_user_id)


class SocialAccountListView(generics.ListAPIView):
    """Vínculos de IdP ativos do usuário logado (seção "Contas conectadas")."""

    permission_classes = [IsAuthenticated]
    serializer_class = SocialAccountSerializer

    def get_queryset(self):
        return SocialAccount.objects.filter(user=self.request.user, active=True)


class AuthEventListView(generics.ListAPIView):
    """Linha do tempo de eventos de autenticação do usuário logado."""

    permission_classes = [IsAuthenticated]
    serializer_class = AuthEventSerializer

    def get_queryset(self):
        return AuthEvent.objects.filter(user=self.request.user)


class ConfirmSocialLinkView(APIView):
    """Confirma o vínculo após o aviso de email-match no login SSO.

    Recebe o token assinado emitido pelo callback, cria/reativa o
    SocialAccount e devolve os JWTs como um login normal.
    """

    permission_classes = []

    @extend_schema(
        request=ConfirmLinkSerializer,
        responses=inline_serializer(
            name="ConfirmLinkResponse",
            fields={
                "access": serializers.CharField(),
                "refresh": serializers.CharField(),
                "user_id": serializers.IntegerField(),
                "username": serializers.CharField(),
                "email": serializers.CharField(),
            },
        ),
    )
    def post(self, request, provider):
        if provider not in ("microsoft", "google"):
            return Response(
                {"detail": "Provider inválido."}, status=status.HTTP_400_BAD_REQUEST
            )

        serializer = ConfirmLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            payload = read_link_token(serializer.validated_data["token"])
        except signing.SignatureExpired:
            return Response(
                {"detail": "Confirmação expirada. Refaça o login."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except signing.BadSignature:
            return Response(
                {"detail": "Token de vínculo inválido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if payload.get("provider") != provider:
            return Response(
                {"detail": "Token de vínculo não corresponde ao provider."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = get_object_or_404(User, id=payload.get("user_id"))
        if not user.is_active or user.merged_into:
            return Response(
                {
                    "detail": (
                        "Esta conta foi fundida em outra. "
                        "Entre pelo login principal."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        sub = payload.get("sub")
        email = payload.get("email")

        if sub:
            existing = SocialAccount.objects.filter(
                provider=provider, provider_user_id=sub
            ).first()
            if existing:
                if existing.user_id != user.id:
                    return Response(
                        {
                            "detail": (
                                "Esta identidade já está vinculada a outra conta. "
                                "A fusão de contas ainda não está disponível."
                            )
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
                if not existing.active or existing.email != email:
                    existing.active = True
                    existing.unlinked_at = None
                    existing.email = email
                    existing.save(update_fields=["active", "unlinked_at", "email"])
            else:
                SocialAccount.objects.create(
                    user=user,
                    provider=provider,
                    provider_user_id=sub,
                    email=email,
                )
            log_auth_event(
                request,
                user,
                "link",
                provider=provider,
                summary=f"Vinculou {provider} ({email})",
                provider_email=email,
            )

        user.auth_provider = provider
        user.save(update_fields=["auth_provider"])
        log_auth_event(
            request,
            user,
            "login_sso",
            provider=provider,
            summary=f"Login via {provider} ({email})",
            provider_email=email,
        )

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user_id": user.id,
                "username": user.username,
                "email": user.email,
            },
            status=status.HTTP_200_OK,
        )


class SocialAccountUnlinkView(APIView):
    """Desvincula um provider — nunca deleta o vínculo nem a conta.

    Se o vínculo é o último método de acesso (sem senha utilizável e sem
    outro provider ativo), exige email+senha nova ou um reset de senha
    para o usuário não se trancar fora.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(request=SocialAccountUnlinkSerializer)
    def post(self, request, pk):
        account = get_object_or_404(
            SocialAccount, pk=pk, user=request.user, active=True
        )
        serializer = SocialAccountUnlinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        has_other = (
            SocialAccount.objects.filter(user=user, active=True)
            .exclude(pk=account.pk)
            .exists()
        )
        if not user.has_usable_password() and not has_other:
            new_password = serializer.validated_data.get("password")
            new_email = serializer.validated_data.get("email")
            wants_reset = serializer.validated_data.get("request_password_reset")
            if not new_password and not wants_reset:
                return Response(
                    {
                        "detail": (
                            "Este é o último método de acesso da conta. "
                            "Defina uma senha ou solicite a redefinição antes "
                            "de desvincular."
                        ),
                        "requires_credential_setup": True,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if new_email:
                user.email = new_email
            if new_password:
                user.set_password(new_password)
            user.save()
            if wants_reset:
                token = default_token_generator.make_token(user)
                reset_link = (
                    f"{settings.FRONTEND_URL}/reset-password"
                    f"?token={user.id}:{token}"
                )
                send_password_reset_email(user, reset_link)

        unlink_social_account(request, account)
        return Response({"detail": "Conta desvinculada."}, status=status.HTTP_200_OK)


class ConfirmMergeView(APIView):
    """Confirma o merge: funde a conta dona da identidade do IdP na conta
    do usuário logado (BE-30). A conta canônica é sempre a autenticada —
    o token só carrega a conta absorvida."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=ConfirmLinkSerializer)
    def post(self, request):
        serializer = ConfirmLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            payload = read_merge_token(serializer.validated_data["token"])
        except signing.SignatureExpired:
            return Response(
                {"detail": "Confirmação expirada. Refaça a conexão."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except signing.BadSignature:
            return Response(
                {"detail": "Token de merge inválido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        absorbed = get_object_or_404(User, id=payload.get("absorbed_id"))
        canonical = request.user
        if absorbed.id == canonical.id:
            return Response(
                {"detail": "A identidade já pertence à sua conta."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not absorbed.is_active:
            return Response(
                {"detail": "Esta conta já foi fundida."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        merge_accounts(request, canonical, absorbed)
        return Response(
            {"detail": f"Conta {absorbed.email} fundida na sua."},
            status=status.HTTP_200_OK,
        )
