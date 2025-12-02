# accounts/views.py
from rest_framework import generics
from accounts.permissions.has_permission import IsOrgAdmin
from accounts.serializers import OrganizationDetailSerializer, OrganizationListSerializer, UserCreateSerializer, UserDetailSerializer, UserListSerializer
from utils.mixins import SerializerByMethodMixin
from .models import Invite, Membership, Organization, Role, User
from rest_framework_simplejwt.views import TokenObtainPairView
from .serializers import CustomTokenObtainPairSerializer, InviteAcceptSerializer, InviteCreateSerializer, InviteSerializer, MembershipCreateSerializer, MembershipSerializer, RoleSerializer, UserMembershipSerializer
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework import status



class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

class OrganizationListCreateView(SerializerByMethodMixin, generics.ListCreateAPIView):
    queryset = Organization.objects.all()
    serializer_class = OrganizationListSerializer
    serializer_map = {
        "POST": OrganizationDetailSerializer
    }

class OrganizationRetrieveUpdateDestroyView(SerializerByMethodMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Organization.objects.all()
    serializer_class = OrganizationDetailSerializer
    serializer_map = {
        "GET": OrganizationDetailSerializer,
        "PUT": OrganizationDetailSerializer,
        "PATCH": OrganizationDetailSerializer,
        "DELETE": OrganizationDetailSerializer,
    }



class UserListCreateView(SerializerByMethodMixin, generics.ListCreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserListSerializer
    serializer_map = {
        "POST": UserCreateSerializer
    }

class UserRetrieveUpdateDestroyView(SerializerByMethodMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserDetailSerializer
    serializer_map = {
        "GET": UserDetailSerializer,
        "PUT": UserCreateSerializer,
        "PATCH": UserCreateSerializer,
        "DELETE": UserDetailSerializer,
    }

class MembershipListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated, IsOrgAdmin]
    """
    Lista todos os membros de uma organização e permite criar novos vínculos.
    """
    serializer_class = MembershipSerializer

    def get_queryset(self):
        org_id = self.kwargs["org_id"]
        return Membership.objects.filter(organization_id=org_id).select_related("user", "role")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return MembershipCreateSerializer
        return MembershipSerializer


class MembershipRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    """
    Recupera, atualiza ou remove um vínculo Membership específico.
    """
    queryset = Membership.objects.all().select_related("user", "role", "organization")
    serializer_class = MembershipSerializer

    def get_serializer_class(self):
        if self.request.method in ["PUT", "PATCH"]:
            return MembershipCreateSerializer
        return MembershipSerializer

class InviteListCreateView(generics.ListCreateAPIView):
    serializer_class = InviteCreateSerializer

    def get_queryset(self):
        org_id = self.kwargs["org_id"]
        return Invite.objects.filter(organization_id=org_id)


class InviteAcceptView(generics.GenericAPIView):
    serializer_class = InviteAcceptSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        result = serializer.save()

        return Response({
            "invite": InviteSerializer(result["invite"]).data,
            "membership": MembershipSerializer(result["membership"]).data
        }, status=status.HTTP_200_OK)



class InviteRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    """
    Recupera, atualiza ou remove um convite específico.
    """
    queryset = Invite.objects.all()
    serializer_class = InviteSerializer

    def perform_destroy(self, instance):
        instance.status = "canceled"
        instance.save()


  
class RoleListCreateView(generics.ListCreateAPIView):
    serializer_class = RoleSerializer
    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def get_queryset(self):
        org_id = self.kwargs["org_id"]
        return Role.objects.filter(organization_id=org_id)

    def perform_create(self, serializer):
        org_id = self.kwargs["org_id"]
        serializer.save(organization_id=org_id)


class RoleRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Role.objects.all()
    serializer_class = RoleSerializer

class UserMembershipListView(generics.ListAPIView):
    serializer_class = UserMembershipSerializer

    def get_queryset(self):
        return Membership.objects.filter(user=self.request.user).select_related("organization", "role")

class PasswordUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        current = request.data.get("current_password")
        new = request.data.get("new_password")

        if not user.check_password(current):
            return Response({"detail": "Senha atual incorreta"}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new)
        user.save()
        return Response({"detail": "Senha atualizada com sucesso"}, status=status.HTTP_200_OK)
  
class RetrieveUserView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserDetailSerializer(request.user, context={"request": request})
        return Response(serializer.data)
