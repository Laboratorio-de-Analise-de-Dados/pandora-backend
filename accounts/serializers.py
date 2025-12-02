# accounts/serializers.py
from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from accounts.services.send_mail import generate_token, send_invite_email
from .models import Invite, Membership, Organization, Role, User
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)

        data.update({
            "user_id": self.user.id,
            "username": self.user.username,
            "email": self.user.email,
        })

        memberships = Membership.objects.filter(user=self.user).select_related("organization", "role")
        data["memberships"] = [
            {
                "organization": m.organization.name,
                "role": m.role.name,
                "status": m.status,
                "permissions": [p.codename for p in m.role.permissions.all()],
            }
            for m in memberships
        ]

        return data
    
class OrganizationListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "org_type"]

class OrganizationDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ["id", "name", "org_type", "external_id"]

class UserListSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = ["id", "username", "email"]

class UserCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["username", "email", "password"]

    def create(self, validated_data):
        # garantir que a senha seja salva corretamente
        user = User(
            username=validated_data["username"],
            email=validated_data.get("email"),
        )
        user.set_password(validated_data["password"])
        user.save()
        return user

class UserDetailSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = ["id", "username", "email"]

class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "name", "permissions"]

class MembershipSerializer(serializers.ModelSerializer):
    user = UserDetailSerializer(read_only=True)
    role = RoleSerializer(read_only=True)
    organization = OrganizationListSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "organization", "role", "status", "joined_at"]

class MembershipCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Membership
        fields = ["user", "organization", "role", "status"]

class InviteSerializer(serializers.ModelSerializer):
    organization = OrganizationListSerializer(read_only=True)
    role = RoleSerializer(read_only=True)

    class Meta:
        model = Invite
        fields = ["id", "email", "organization", "role", "token", "status", "created_at", "expires_at"]
        read_only_fields = ["token", "status", "created_at", "expires_at"]

class UserMembershipSerializer(serializers.ModelSerializer):
    organization = OrganizationListSerializer(read_only=True)
    role = RoleSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "organization", "role", "status", "joined_at"]

class InviteCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invite
        fields = ["id", "email", "organization", "role"]

    def validate(self, attrs):
        email = attrs.get("email")
        organization = attrs.get("organization")
        role = attrs.get("role")

        # Verifica se já existe convite pendente para esse vínculo
        if Invite.objects.filter(
            email=email,
            organization=organization,
            role=role,
            status="pending"
        ).exists():
            raise serializers.ValidationError(
                "Já existe um convite pendente para este usuário nesta organização e role."
            )

        return attrs

    def create(self, validated_data):
        # Cria o convite com token e status inicial
        invite = Invite.objects.create(
            email=validated_data["email"],
            organization=validated_data["organization"],
            role=validated_data["role"],
            token=generate_token(),
            status="pending",
        )
        # Envia o e-mail
        send_invite_email(invite)
        return invite

class InviteAcceptSerializer(serializers.Serializer):
    token = serializers.CharField()

    def validate_token(self, value):
        try:
            invite = Invite.objects.select_related("organization", "role").get(token=value)
        except Invite.DoesNotExist:
            raise serializers.ValidationError("Convite inválido.")

        if invite.status == "accepted":
            # ✅ idempotência: já aceito, não precisa erro
            self.invite = invite
            return value

        if invite.status != "pending":
            raise serializers.ValidationError("Convite já foi processado.")

        if invite.expires_at and invite.expires_at < timezone.now():
            raise serializers.ValidationError("Convite expirado.")

        self.invite = invite
        return value

    def validate(self, attrs):
        user = self.context["request"].user
        if not user or not user.is_authenticated:
            raise serializers.ValidationError({"auth": "Usuário não autenticado. Faça login para aceitar."})

        attrs["invite"] = self.invite
        attrs["user"] = user
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        invite: Invite = validated_data["invite"]
        user = validated_data["user"]

        membership, _ = Membership.objects.update_or_create(
            user=user,
            organization=invite.organization,
            defaults={"role": invite.role, "status": "active"}
        )

        if invite.status == "pending":
            invite.status = "accepted"
            invite.save(update_fields=["status"])

        return {"invite": invite, "membership": membership}