# accounts/views.py
from rest_framework import generics
from accounts.serializers import OrganizationDetailSerializer, OrganizationListSerializer, UserCreateSerializer, UserDetailSerializer, UserListSerializer
from utils.mixins import SerializerByMethodMixin
from .models import Organization, User
from rest_framework_simplejwt.views import TokenObtainPairView
from .serializers import CustomTokenObtainPairSerializer


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

