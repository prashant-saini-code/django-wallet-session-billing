from rest_framework import serializers
from rest_framework.authtoken.models import Token
from .models import User, UserRole


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6)
    token = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ("id", "username", "email", "password", "role", "token")
        extra_kwargs = {
            "email": {"required": False},
            "role": {"default": UserRole.USER},
        }

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User.objects.create_user(
            password=password,
            **validated_data,
        )
        token, _ = Token.objects.get_or_create(user=user)
        user.token = token.key
        return user


class UserDetailSerializer(serializers.ModelSerializer):
    wallet_balance = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "username", "email", "role", "wallet_balance")

    def get_wallet_balance(self, obj) -> str:
        if hasattr(obj, "wallet"):
            return str(obj.wallet.balance)
        return "0.00"
