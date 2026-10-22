from decimal import Decimal
from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token
from users.models import User, UserRole
from wallets.services import credit_wallet, get_or_create_wallet


class Command(BaseCommand):
    help = "Seeds initial demo users, providers, and funded wallets for testing and evaluation."

    def handle(self, *args, **options):
        # Create or update Client User
        client, created = User.objects.get_or_create(
            username="client1",
            defaults={"email": "client1@example.com", "role": UserRole.USER},
        )
        client.set_password("password123")
        client.save()
        client_token, _ = Token.objects.get_or_create(user=client)
        wallet = get_or_create_wallet(client)
        if wallet.balance < Decimal("500.00"):
            wallet = credit_wallet(wallet, Decimal("500.00"), reference="SEED-INIT", description="Initial seed credit")
        wallet.refresh_from_db()

        # Create or update Provider 1
        prov1, _ = User.objects.get_or_create(
            username="dr_sarah",
            defaults={"email": "sarah@example.com", "role": UserRole.PROVIDER},
        )
        prov1.set_password("password123")
        prov1.save()
        prov1_token, _ = Token.objects.get_or_create(user=prov1)

        # Create or update Provider 2
        prov2, _ = User.objects.get_or_create(
            username="dr_alex",
            defaults={"email": "alex@example.com", "role": UserRole.PROVIDER},
        )
        prov2.set_password("password123")
        prov2.save()
        prov2_token, _ = Token.objects.get_or_create(user=prov2)

        self.stdout.write(self.style.SUCCESS("Successfully seeded demo data:"))
        self.stdout.write(f"  Client:   username='client1', password='password123', Token='{client_token.key}' (Wallet balance: {wallet.balance})")
        self.stdout.write(f"  Provider: username='dr_sarah', password='password123', Token='{prov1_token.key}'")
        self.stdout.write(f"  Provider: username='dr_alex', password='password123', Token='{prov2_token.key}'")
