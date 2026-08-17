from datetime import timedelta
from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from django.contrib.auth.tokens import default_token_generator
from accounts.services.send_mail import generate_token, send_invite_email
from .models import Invite, Membership, Organization, Role, User
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


def get_or_create_default_roles():
    """Garante que os papéis básicos existam no banco."""
    roles = {}
    for name in (Role.SUPER_ADMIN, Role.ORG_ADMIN, Role.MEMBER):
        role, _ = Role.objects.get_or_create(name=name)
        roles[name] = role
    return roles


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)

        data.update(
            {
                "user_id": self.user.id,
                "username": self.user.username,
                "email": self.user.email,
                "is_super_admin": self.user.is_super_admin,
                "auth_provider": self.user.auth_provider,
            }
        )

        memberships = Membership.objects.filter(
            user=self.user, status="active"
        ).select_related("organization", "role")
        data["memberships"] = [
            {
                "id": m.id,
                "organization": {"id": m.organization.id, "name": m.organization.name},
                "role": m.role.name,
            }
            for m in memberships
        ]

        return data


class OrganizationListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "org_type"]


class OrganizationDetailSerializer(serializers.ModelSerializer):
    members = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = ["id", "name", "org_type", "external_id", "members"]

    def get_members(self, obj):
        memberships = obj.memberships.filter(status="active").select_related(
            "user", "role"
        )
        return MembershipSerializer(memberships, many=True).data


class UserListSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email"]


class UserCreateSerializer(serializers.ModelSerializer):
    organization_id = serializers.IntegerField(
        write_only=True, required=False, allow_null=True
    )
    role = serializers.CharField(write_only=True, required=False, default=Role.MEMBER)

    class Meta:
        model = User
        fields = ["username", "email", "password", "organization_id", "role"]
        extra_kwargs = {"password": {"write_only": True}}

    def validate_role(self, value):
        if value not in (Role.SUPER_ADMIN, Role.ORG_ADMIN, Role.MEMBER):
            raise serializers.ValidationError("Role inválida.")
        return value

    def create(self, validated_data):
        organization_id = validated_data.pop("organization_id", None)
        role_name = validated_data.pop("role", Role.MEMBER)

        user = User(
            username=validated_data["username"],
            email=validated_data.get("email"),
        )
        user.set_password(validated_data["password"])
        user.save()

        if organization_id:
            roles = get_or_create_default_roles()
            role = roles.get(role_name, roles[Role.MEMBER])
            Membership.objects.create(
                user=user,
                organization_id=organization_id,
                role=role,
                status="active",
            )

        return user


class UserRegisterSerializer(serializers.ModelSerializer):
    organization_id = serializers.IntegerField(
        write_only=True, required=False, allow_null=True, default=None
    )
    role = serializers.CharField(
        write_only=True, required=False, allow_blank=True, default=""
    )
    invite_token = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "password",
            "organization_id",
            "role",
            "invite_token",
        ]
        extra_kwargs = {"password": {"write_only": True}}

    def validate_role(self, value):
        if value and value not in (Role.ORG_ADMIN, Role.MEMBER):
            raise serializers.ValidationError("Role inválida para cadastro público.")
        return value

    def validate(self, attrs):
        if attrs.get("organization_id") and not attrs.get("invite_token"):
            raise serializers.ValidationError(
                {"organization_id": "Não é possível escolher organização sem convite."}
            )
        if attrs.get("role") and not attrs.get("invite_token"):
            raise serializers.ValidationError(
                {"role": "Não é possível escolher role sem convite."}
            )
        return attrs

    def validate_invite_token(self, value):
        try:
            invite = Invite.objects.select_related("organization", "role").get(
                token=value, status="pending"
            )
        except Invite.DoesNotExist:
            raise serializers.ValidationError("Convite inválido ou já processado.")
        if invite.expires_at and invite.expires_at < timezone.now():
            raise serializers.ValidationError("Convite expirado.")

        provided_email = self.initial_data.get("email", "")
        if provided_email and provided_email.lower() != invite.email.lower():
            raise serializers.ValidationError("Este convite pertence a outro email.")

        self.invite = invite
        return value

    def create(self, validated_data):
        organization_id = validated_data.pop("organization_id", None)
        role_name = validated_data.pop("role") or Role.MEMBER
        validated_data.pop("invite_token", None)
        invite = getattr(self, "invite", None)

        user = User(
            username=validated_data["username"],
            email=validated_data.get("email"),
        )
        user.set_password(validated_data["password"])
        user.save()

        if invite:
            Membership.objects.update_or_create(
                user=user,
                organization=invite.organization,
                defaults={"role": invite.role, "status": "active"},
            )
            if invite.status == "pending":
                invite.status = "accepted"
                invite.save(update_fields=["status"])
        elif organization_id:
            roles = get_or_create_default_roles()
            role = roles.get(role_name, roles[Role.MEMBER])
            Membership.objects.create(
                user=user,
                organization_id=organization_id,
                role=role,
                status="active",
            )

        return user


class UserDetailSerializer(serializers.ModelSerializer):
    memberships = MembershipSerializer(source="memberships", many=True, read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "is_super_admin", "auth_provider", "memberships"]


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "name", "permissions"]


class MembershipSerializer(serializers.ModelSerializer):
    user = UserListSerializer(read_only=True)
    role = RoleSerializer(read_only=True)
    organization = OrganizationListSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "organization", "role", "status", "joined_at"]


class MembershipCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Membership
        fields = ["user", "role", "status"]


class InviteSerializer(serializers.ModelSerializer):
    organization = OrganizationListSerializer(read_only=True)
    role = RoleSerializer(read_only=True)

    class Meta:
        model = Invite
        fields = [
            "id",
            "email",
            "organization",
            "role",
            "token",
            "status",
            "created_at",
            "expires_at",
        ]
        read_only_fields = ["token", "status", "created_at", "expires_at"]


class UserMembershipSerializer(serializers.ModelSerializer):
    organization = OrganizationListSerializer(read_only=True)
    role = RoleSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "organization", "role", "status", "joined_at"]


class InviteCreateSerializer(serializers.ModelSerializer):
    organization_id = serializers.IntegerField(write_only=True, required=False)
    role = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = Invite
        fields = ["id", "email", "organization_id", "role"]

    def validate(self, attrs):
        email = attrs.get("email")
        organization_id = attrs.get("organization_id")
        role_name = attrs.get("role")

        if organization_id:
            organization = Organization.objects.filter(id=organization_id).first()
            if not organization:
                raise serializers.ValidationError(
                    {"organization": "Organização inválida."}
                )
            attrs["organization"] = organization

        if role_name:
            roles = get_or_create_default_roles()
            role = roles.get(role_name)
            if not role:
                raise serializers.ValidationError({"role": "Role inválida."})
            attrs["role"] = role

        return attrs

    def create(self, validated_data):
        organization_id = validated_data.get("organization_id")
        role_name = validated_data.get("role") or Role.MEMBER

        organization = Organization.objects.filter(id=organization_id).first()
        if not organization:
            raise serializers.ValidationError({"organization": "Organização inválida."})

        roles = get_or_create_default_roles()
        role = roles.get(role_name)
        if not role:
            raise serializers.ValidationError({"role": "Role inválida."})

        if Invite.objects.filter(
            email=validated_data["email"],
            organization=organization,
            role=role,
            status="pending",
        ).exists():
            raise serializers.ValidationError(
                "Já existe um convite pendente para este usuário nesta organização e role."
            )

        invite = Invite.objects.create(
            email=validated_data["email"],
            organization=organization,
            role=role,
            token=generate_token(),
            status="pending",
            expires_at=timezone.now() + timedelta(hours=24),
        )
        send_invite_email(invite)
        return invite


class InviteAcceptSerializer(serializers.Serializer):
    token = serializers.CharField()

    def validate_token(self, value):
        try:
            invite = Invite.objects.select_related("organization", "role").get(
                token=value
            )
        except Invite.DoesNotExist:
            raise serializers.ValidationError("Convite inválido.")

        if invite.status != "pending":
            raise serializers.ValidationError("Convite já foi processado ou cancelado.")

        if invite.expires_at and invite.expires_at < timezone.now():
            raise serializers.ValidationError("Convite expirado.")

        self.invite = invite
        return value

    def validate(self, attrs):
        user = self.context["request"].user
        if not user or not user.is_authenticated:
            raise serializers.ValidationError(
                {"auth": "Usuário não autenticado. Faça login para aceitar."}
            )

        if user.email.lower() != self.invite.email.lower():
            raise serializers.ValidationError(
                {"email": "Este convite pertence a outro email."}
            )

        attrs["invite"] = self.invite
        attrs["user"] = user
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        invite = validated_data["invite"]
        user = validated_data["user"]

        membership, _ = Membership.objects.update_or_create(
            user=user,
            organization=invite.organization,
            defaults={"role": invite.role, "status": "active"},
        )

        if invite.status == "pending":
            invite.status = "accepted"
            invite.save(update_fields=["status"])

        return {"invite": invite, "membership": membership}


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=6)

    def validate_token(self, value):
        try:
            user_id, token = value.split(":", 1)
            self.user = User.objects.get(pk=user_id)
        except (ValueError, User.DoesNotExist):
            raise serializers.ValidationError("Token inválido.")

        self.token = token
        if not default_token_generator.check_token(self.user, token):
            raise serializers.ValidationError("Token expirado ou inválido.")
        return value

    def save(self, **kwargs):
        password = self.validated_data["new_password"]
        self.user.set_password(password)
        self.user.save(update_fields=["password"])
        return self.user
