from rest_framework import generics, permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from .models import User
from .serializers import UserDetailSerializer, UserRegistrationSerializer


class RegisterView(generics.CreateAPIView):
    """Register a new user or provider."""
    queryset = User.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]


class CustomAuthTokenView(ObtainAuthToken):
    """Authenticate with username and password, returns token and role."""

    @extend_schema(
        request=inline_serializer(
            name="LoginRequest",
            fields={"username": serializers.CharField(), "password": serializers.CharField()},
        ),
        responses=inline_serializer(
            name="LoginResponse",
            fields={
                "token": serializers.CharField(),
                "user_id": serializers.IntegerField(),
                "username": serializers.CharField(),
                "role": serializers.ChoiceField(choices=["USER", "PROVIDER"]),
            },
        ),
    )
    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {
                "token": token.key,
                "user_id": user.pk,
                "username": user.username,
                "role": user.role,
            },
            status=status.HTTP_200_OK,
        )


class MeView(generics.RetrieveAPIView):
    """Get current authenticated user profile."""
    serializer_class = UserDetailSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class ProviderListView(generics.ListAPIView):
    """List all available providers."""
    serializer_class = UserDetailSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return User.objects.filter(role="PROVIDER").order_by("username")
