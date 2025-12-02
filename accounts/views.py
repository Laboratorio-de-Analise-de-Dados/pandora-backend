# accounts/views.py
from django.views import generic

from accounts.serializers import OrganizationDetailSerializer, OrganizationListSerializer, UserCreateSerializer, UserDetailSerializer, UserListSerializer
from utils.mixins import SerializerByMethodMixin

from .models import Organization, User
from django.urls import reverse_lazy


class OrganizationListCreateView(SerializerByMethodMixin, generic.ListCreateAPIView):
    queryset = Organization.objects.all()
    serializer_class = OrganizationListSerializer
    serializer_map = {
        "POST": OrganizationDetailSerializer
    }

class OrganizationRetrieveUpdateDestroyView(SerializerByMethodMixin, generic.RetrieveUpdateDestroyAPIView):
    queryset = Organization.objects.all()
    serializer_class = OrganizationDetailSerializer
    serializer_map = {
        "GET": OrganizationDetailSerializer,
        "PUT": OrganizationDetailSerializer,
        "PATCH": OrganizationDetailSerializer,
        "DELETE": OrganizationDetailSerializer,
    }



class UserListCreateView(SerializerByMethodMixin, generic.ListCreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserListSerializer
    serializer_map = {
        "POST": UserCreateSerializer
    }

class UserRetrieveUpdateDestroyView(SerializerByMethodMixin, generic.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserDetailSerializer
    serializer_map = {
        "GET": UserDetailSerializer,
        "PUT": UserCreateSerializer,
        "PATCH": UserCreateSerializer,
        "DELETE": UserDetailSerializer,
    }

