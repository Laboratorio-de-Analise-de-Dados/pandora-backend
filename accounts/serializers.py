# accounts/serializers.py
from rest_framework import serializers
from .models import Organization, User

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
