"""
URL configuration for config project.
"""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("", TemplateView.as_view(template_name="demo.html"), name="api-playground"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("admin/", admin.site.urls),
    path("api/auth/", include("users.urls")),
    path("api/wallet/", include("wallets.urls")),
    path("api/sessions/", include("consultations.urls")),
]
