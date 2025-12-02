from django.urls import path
from .views import (
    CustomTokenObtainPairView, OrganizationListCreateView, OrganizationRetrieveUpdateDestroyView, UserListCreateView, 
    UserRetrieveUpdateDestroyView
)
from rest_framework_simplejwt.views import (
    TokenRefreshView,
)


urlpatterns = [
    path("organizations/", OrganizationListCreateView.as_view(), name="organization_list_create"),
    path("organizations/<int:pk>/", OrganizationRetrieveUpdateDestroyView.as_view(), name="organization_detail"),
    path("users/", UserListCreateView.as_view(), name="user_list_create"),
    path("users/<int:pk>/", UserRetrieveUpdateDestroyView.as_view(), name="user_detail"),
    path("login/", CustomTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("refresh/", TokenRefreshView.as_view(), name="token_refresh"),
]
