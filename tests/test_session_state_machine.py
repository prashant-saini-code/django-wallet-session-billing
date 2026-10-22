from decimal import Decimal
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase
from consultations.models import ConsultationSession, SessionEndedBy, SessionStatus
from consultations.services import (
    ActiveSessionExistsError,
    InvalidStateTransitionError,
    accept_session,
    assign_provider,
    create_consultation_session,
    end_session_by_user,
    start_session,
)
from users.models import User, UserRole
from wallets.services import credit_wallet, get_or_create_wallet


class SessionStateMachineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="client_user",
            password="password123",
            role=UserRole.USER,
        )
        self.provider = User.objects.create_user(
            username="dr_smith",
            password="password123",
            role=UserRole.PROVIDER,
        )
        self.wallet = get_or_create_wallet(self.user)
        # Fund wallet with sufficient balance for testing
        credit_wallet(self.wallet, Decimal("500.00"))

    def test_complete_valid_lifecycle(self):
        # 1. Create session (unassigned)
        session = create_consultation_session(user=self.user)
        self.assertEqual(session.status, SessionStatus.UNASSIGNED)

        # 2. Assign provider -> assigned
        session = assign_provider(session=session, provider=self.provider, actor=self.user)
        self.assertEqual(session.status, SessionStatus.ASSIGNED)
        self.assertEqual(session.provider, self.provider)

        # 3. Provider accepts -> accepted_by_provider
        session = accept_session(session=session, provider=self.provider)
        self.assertEqual(session.status, SessionStatus.ACCEPTED_BY_PROVIDER)

        # 4. User starts session -> in_progress
        session = start_session(session=session, actor=self.user)
        self.assertEqual(session.status, SessionStatus.IN_PROGRESS)
        self.assertIsNotNone(session.started_at)

        # 5. User ends session -> completed
        session = end_session_by_user(session=session, actor=self.user)
        self.assertEqual(session.status, SessionStatus.COMPLETED)
        self.assertEqual(session.ended_by, SessionEndedBy.USER)
        self.assertIsNotNone(session.ended_at)

    def test_scenario_4_invalid_state_transition_ending_before_start_raises_error(self):
        """
        Required Test Scenario 4:
        Attempt an invalid action, such as ending a session before it has started.
        Verify that the operation fails with invalid state error.
        """
        session = create_consultation_session(user=self.user, provider=self.provider)
        # Status is 'assigned' (not in_progress)
        self.assertEqual(session.status, SessionStatus.ASSIGNED)

        with self.assertRaises(InvalidStateTransitionError) as context:
            end_session_by_user(session=session, actor=self.user)

        self.assertIn("Cannot end session in 'assigned' state", str(context.exception))

    def test_cannot_start_session_without_provider_acceptance(self):
        session = create_consultation_session(user=self.user, provider=self.provider)
        # Status is 'assigned'
        with self.assertRaises(InvalidStateTransitionError):
            start_session(session=session, actor=self.user)

    def test_cannot_have_more_than_one_active_session_simultaneously(self):
        """A user cannot have more than one active (in_progress) session at the same time."""
        # Start session 1
        s1 = create_consultation_session(user=self.user, provider=self.provider)
        s1 = accept_session(s1, self.provider)
        s1 = start_session(s1, self.user)
        self.assertEqual(s1.status, SessionStatus.IN_PROGRESS)

        # Try to start session 2 for the same user
        s2 = create_consultation_session(user=self.user, provider=self.provider)
        s2 = accept_session(s2, self.provider)

        with self.assertRaises(ActiveSessionExistsError):
            start_session(s2, self.user)


class SessionAPIStateMachineTests(APITestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            username="patient_pete",
            password="password123",
            role=UserRole.USER,
        )
        self.provider_user = User.objects.create_user(
            username="dr_jane",
            password="password123",
            role=UserRole.PROVIDER,
        )
        self.wallet = get_or_create_wallet(self.client_user)
        credit_wallet(self.wallet, Decimal("500.00"))

    def test_scenario_4_api_ending_before_start_returns_400(self):
        """
        Required Test Scenario 4 via API:
        Attempt an invalid action, such as ending a session before it has started.
        Verify that the request returns HTTP 400.
        """
        self.client.force_authenticate(user=self.client_user)

        # Create session
        create_res = self.client.post("/api/sessions/", {"provider_id": self.provider_user.id})
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)
        session_id = create_res.data["id"]

        # Attempt to end immediately without starting
        end_res = self.client.post(f"/api/sessions/{session_id}/end/")
        self.assertEqual(end_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", end_res.data)
        self.assertIn("Cannot end session in 'assigned' state", end_res.data["error"])

    def test_api_invalid_transition_starting_unassigned_returns_400(self):
        self.client.force_authenticate(user=self.client_user)

        # Create unassigned session
        create_res = self.client.post("/api/sessions/")
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)
        session_id = create_res.data["id"]

        # Attempt to start directly
        start_res = self.client.post(f"/api/sessions/{session_id}/start/")
        self.assertEqual(start_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cannot start session in 'unassigned' state", start_res.data["error"])

    def test_session_cancellation_lifecycle(self):
        """Pre-session cancellation succeeds; in-progress cancellation returns 400."""
        self.client.force_authenticate(user=self.client_user)

        # 1. Create and cancel before starting
        res1 = self.client.post("/api/sessions/", {"provider_id": self.provider_user.id})
        s1_id = res1.data["id"]

        cancel_res = self.client.post(f"/api/sessions/{s1_id}/cancel/")
        self.assertEqual(cancel_res.status_code, status.HTTP_200_OK)
        self.assertEqual(cancel_res.data["status"], "cancelled")

        # 2. Cannot cancel in_progress session
        res2 = self.client.post("/api/sessions/", {"provider_id": self.provider_user.id})
        s2_id = res2.data["id"]
        s2 = ConsultationSession.objects.get(id=s2_id)
        accept_session(s2, self.provider_user)
        start_session(s2, self.client_user)

        cancel_active_res = self.client.post(f"/api/sessions/{s2_id}/cancel/")
        self.assertEqual(cancel_active_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cannot cancel session in 'in_progress' state", cancel_active_res.data["error"])

    def test_create_session_rejects_invalid_payload_values(self):
        self.client.force_authenticate(user=self.client_user)

        invalid_provider = self.client.post("/api/sessions/", {"provider_id": 0})
        self.assertEqual(invalid_provider.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("provider_id", invalid_provider.data)

        invalid_cost = self.client.post("/api/sessions/", {"per_minute_cost": "0.00"})
        self.assertEqual(invalid_cost.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("per_minute_cost", invalid_cost.data)
