from django.contrib.auth.models import AbstractUser
from django.db import models


class UserRole(models.TextChoices):
    USER = "USER", "User"
    PROVIDER = "PROVIDER", "Provider"


class User(AbstractUser):
    """
    Custom user model distinguishing between regular USER and PROVIDER roles.
    """
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.USER,
        help_text="Role of the user in the system (USER or PROVIDER).",
    )

    @property
    def is_provider_role(self) -> bool:
        return self.role == UserRole.PROVIDER

    @property
    def is_user_role(self) -> bool:
        return self.role == UserRole.USER

    def __str__(self):
        return f"{self.username} [{self.role}]"
