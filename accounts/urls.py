from django.urls import path
from .views import (
    OrganizationListCreateView, OrganizationRetrieveUpdateDestroyView, UserListCreateView, 
    UserRetrieveUpdateDestroyView
)

urlpatterns = [
    path("organizations/", OrganizationListCreateView.as_view(), name="organization_list_create"),
    path("organizations/<int:pk>/", OrganizationRetrieveUpdateDestroyView.as_view(), name="organization_detail"),
    path("users/", UserListCreateView.as_view(), name="user_list_create"),
    path("users/<int:pk>/", UserRetrieveUpdateDestroyView.as_view(), name="user_detail"),
]
