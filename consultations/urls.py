from django.urls import path
from .views import (
    AcceptSessionView,
    AssignProviderView,
    BillSessionView,
    CancelSessionView,
    EndSessionView,
    SessionDetailView,
    SessionListCreateView,
    StartSessionView,
)

urlpatterns = [
    path("", SessionListCreateView.as_view(), name="session-list-create"),
    path("<int:pk>/", SessionDetailView.as_view(), name="session-detail"),
    path("<int:pk>/assign/", AssignProviderView.as_view(), name="session-assign"),
    path("<int:pk>/accept/", AcceptSessionView.as_view(), name="session-accept"),
    path("<int:pk>/start/", StartSessionView.as_view(), name="session-start"),
    path("<int:pk>/end/", EndSessionView.as_view(), name="session-end"),
    path("<int:pk>/bill/", BillSessionView.as_view(), name="session-bill"),
    path("<int:pk>/cancel/", CancelSessionView.as_view(), name="session-cancel"),
]
