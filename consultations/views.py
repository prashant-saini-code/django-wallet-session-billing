from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from users.models import User
from .models import ConsultationSession
from .serializers import (
    AssignProviderSerializer,
    BillSessionSerializer,
    ConsultationSessionSerializer,
    CreateSessionSerializer,
)
from .services import (
    ActiveSessionExistsError,
    InsufficientBalanceError,
    InvalidStateTransitionError,
    SessionPermissionError,
    accept_session,
    assign_provider,
    bill_session,
    cancel_session,
    create_consultation_session,
    end_session_by_provider,
    end_session_by_user,
    start_session,
)


class SessionListCreateView(APIView):
    """
    List user's sessions or create a new session.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=ConsultationSessionSerializer(many=True))
    def get(self, request):
        user = request.user
        if user.role == "PROVIDER":
            sessions = ConsultationSession.objects.filter(provider=user)
        else:
            sessions = ConsultationSession.objects.filter(user=user)
        serializer = ConsultationSessionSerializer(sessions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(request=CreateSessionSerializer, responses={201: ConsultationSessionSerializer})
    def post(self, request):
        serializer = CreateSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        provider_id = serializer.validated_data.get("provider_id")
        per_minute_cost = serializer.validated_data.get("per_minute_cost")

        provider = None
        if provider_id is not None:
            provider = get_object_or_404(User, pk=provider_id)

        try:
            session = create_consultation_session(
                user=request.user,
                provider=provider,
                per_minute_cost=per_minute_cost,
            )
        except (SessionPermissionError, ValueError) as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            ConsultationSessionSerializer(session).data,
            status=status.HTTP_201_CREATED,
        )


class SessionDetailView(APIView):
    """
    Retrieve details of a consultation session including billing records.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=ConsultationSessionSerializer)
    def get(self, request, pk):
        session = get_object_or_404(ConsultationSession, pk=pk)
        # Verify access
        if session.user_id != request.user.id and session.provider_id != request.user.id and not request.user.is_staff:
            return Response(
                {"error": "You do not have permission to view this session."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ConsultationSessionSerializer(session)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AssignProviderView(APIView):
    """
    Assign a provider to an unassigned session.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=AssignProviderSerializer, responses=ConsultationSessionSerializer)
    def post(self, request, pk):
        session = get_object_or_404(ConsultationSession, pk=pk)
        serializer = AssignProviderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        provider = get_object_or_404(User, pk=serializer.validated_data["provider_id"])

        try:
            session = assign_provider(session=session, provider=provider, actor=request.user)
        except (InvalidStateTransitionError, SessionPermissionError, ValueError) as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ConsultationSessionSerializer(session).data, status=status.HTTP_200_OK)


class AcceptSessionView(APIView):
    """
    Assigned provider accepts the session.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=ConsultationSessionSerializer)
    def post(self, request, pk):
        session = get_object_or_404(ConsultationSession, pk=pk)

        try:
            session = accept_session(session=session, provider=request.user)
        except (InvalidStateTransitionError, SessionPermissionError) as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ConsultationSessionSerializer(session).data, status=status.HTTP_200_OK)


class StartSessionView(APIView):
    """
    Client starts the session.
    Verifies minimum wallet balance (5x per-minute cost) and no active sessions.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=ConsultationSessionSerializer)
    def post(self, request, pk):
        session = get_object_or_404(ConsultationSession, pk=pk)

        try:
            session = start_session(session=session, actor=request.user)
        except (
            InvalidStateTransitionError,
            InsufficientBalanceError,
            ActiveSessionExistsError,
            SessionPermissionError,
        ) as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ConsultationSessionSerializer(session).data, status=status.HTTP_200_OK)


class EndSessionView(APIView):
    """
    Client or Provider ends the active session.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=ConsultationSessionSerializer)
    def post(self, request, pk):
        session = get_object_or_404(ConsultationSession, pk=pk)

        try:
            if request.user.role == "PROVIDER":
                session = end_session_by_provider(session=session, actor=request.user)
            else:
                session = end_session_by_user(session=session, actor=request.user)
        except (InvalidStateTransitionError, SessionPermissionError) as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ConsultationSessionSerializer(session).data, status=status.HTTP_200_OK)


class BillSessionView(APIView):
    """
    Synchronous billing endpoint triggered periodically during an active session.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=BillSessionSerializer,
        responses=inline_serializer(
            name="BillSessionResponse",
            fields={
                "billing_summary": inline_serializer(
                    name="BillingSummary",
                    fields={
                        "billed_minutes_count": serializers.IntegerField(),
                        "session_ended": serializers.BooleanField(),
                        "reason": serializers.CharField(allow_null=True),
                        "total_billed_amount": serializers.DecimalField(max_digits=12, decimal_places=2),
                        "session_status": serializers.CharField(),
                    },
                ),
                "session": ConsultationSessionSerializer(),
            },
        ),
    )
    def post(self, request, pk):
        session = get_object_or_404(ConsultationSession, pk=pk)

        # Ensure only participants or admin can trigger billing
        if session.user_id != request.user.id and session.provider_id != request.user.id and not request.user.is_staff:
            return Response(
                {"error": "You do not have permission to trigger billing on this session."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = BillSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        target_minutes = serializer.validated_data.get("minutes")
        already_billed = session.billed_minutes
        additional_minutes = (
            max(0, target_minutes - already_billed)
            if target_minutes is not None
            else None
        )

        try:
            result = bill_session(
                session=session,
                minutes_count=additional_minutes,
            )
        except (InvalidStateTransitionError, ValueError) as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        session.refresh_from_db()
        return Response(
            {
                "billing_summary": result,
                "session": ConsultationSessionSerializer(session).data,
            },
            status=status.HTTP_200_OK,
        )


class CancelSessionView(APIView):
    """
    Cancel an unstarted session.
    """
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=ConsultationSessionSerializer)
    def post(self, request, pk):
        session = get_object_or_404(ConsultationSession, pk=pk)

        try:
            session = cancel_session(session=session, actor=request.user)
        except (InvalidStateTransitionError, SessionPermissionError) as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ConsultationSessionSerializer(session).data, status=status.HTTP_200_OK)
