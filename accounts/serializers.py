# accounts/serializers.py
from rest_framework import serializers
from .models import Membership, Organization, User
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Serializer customizado para incluir dados extras no JWT.
    Agora preparado para Membership e Role.
    """
    def validate(self, attrs):
        data = super().validate(attrs)

        # Informações básicas do usuário
        data.update({
            "user_id": self.user.id,
            "username": self.user.username,
            "email": self.user.email,
        })

        # Se ainda estiver usando User.organization e User.role simples
        data.update({
            "organization": self.user.organization.name if self.user.organization else None,
            "role": self.user.role,
        })

        # Se já estiver usando Membership (n:n entre User ↔ Org ↔ Role)
        memberships = Membership.objects.filter(user=self.user).select_related("organization", "role")
        data["memberships"] = [
            {
                "organization": m.organization.name,
                "role": m.role.name,
                "permissions": [p.codename for p in m.role.permissions.all()]
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
    organization_name = serializers.CharField(source="organization.name", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "role", "organization_name"]

class UserCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["username", "email", "password", "role", "organization"]

    def create(self, validated_data):
        # garantir que a senha seja salva corretamente
        user = User(
            username=validated_data["username"],
            email=validated_data.get("email"),
            role=validated_data.get("role"),
            organization=validated_data.get("organization"),
        )
        user.set_password(validated_data["password"])
        user.save()
        return user

class UserDetailSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "role", "organization_name"]
