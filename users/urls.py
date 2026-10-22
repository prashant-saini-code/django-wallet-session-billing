from django.urls import path
from .views import CustomAuthTokenView, MeView, ProviderListView, RegisterView

urlpatterns = [
    path("register/", RegisterView.as_view(), name="user-register"),
    path("login/", CustomAuthTokenView.as_view(), name="user-login"),
    path("me/", MeView.as_view(), name="user-me"),
    path("providers/", ProviderListView.as_view(), name="provider-list"),
]
