from datetime import date, timedelta

from django.db.models import Count, Q
from rest_framework import mixins, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.integrations.models import Listing, ListingChannel
from apps.landlords.models import Landlord, LandlordRepresentative, Lease
from apps.operations.models import CleaningDuty, Ticket
from apps.people.models import Person
from apps.properties.models import Property, Space
from apps.records.models import Correspondence, RecordFile
from apps.tenancies.models import Payment, Tenancy
from apps.workspaces.permissions import HasWorkspace, IsTenant, IsWorkspaceStaff

from . import serializers as s


class ScopedViewSet(viewsets.ModelViewSet):
    """CRUD limited to the caller's workspace; DELETE archives."""

    permission_classes = [IsAuthenticated, HasWorkspace, IsWorkspaceStaff]
    model = None

    def get_queryset(self):
        return self.model.objects.active().filter(workspace=self.request.workspace)

    def perform_create(self, serializer):
        serializer.save(workspace=self.request.workspace)


def viewset(model_cls, serializer_cls, **extra):
    return type(
        f"{model_cls.__name__}ViewSet",
        (ScopedViewSet,),
        {"model": model_cls, "serializer_class": serializer_cls, **extra},
    )


PropertyViewSet = viewset(Property, s.PropertySerializer)
SpaceViewSet = viewset(Space, s.SpaceSerializer)
LandlordViewSet = viewset(Landlord, s.LandlordSerializer)
LandlordRepresentativeViewSet = viewset(LandlordRepresentative, s.LandlordRepresentativeSerializer)
LeaseViewSet = viewset(Lease, s.LeaseSerializer)
PersonViewSet = viewset(Person, s.PersonSerializer)
TenancyViewSet = viewset(Tenancy, s.TenancySerializer)
PaymentViewSet = viewset(Payment, s.PaymentSerializer)
TicketViewSet = viewset(Ticket, s.TicketSerializer)
CleaningDutyViewSet = viewset(CleaningDuty, s.CleaningDutySerializer)
CorrespondenceViewSet = viewset(Correspondence, s.CorrespondenceSerializer)
RecordFileViewSet = viewset(RecordFile, s.RecordFileSerializer)
ListingViewSet = viewset(Listing, s.ListingSerializer)
ListingChannelViewSet = viewset(ListingChannel, s.ListingChannelSerializer)


@api_view(["GET"])
@permission_classes([IsAuthenticated, HasWorkspace, IsWorkspaceStaff])
def dashboard(request):
    ws = request.workspace
    today = date.today()
    spaces = Space.objects.active().filter(workspace=ws)
    by_status = dict(spaces.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
    leases = Lease.objects.active().filter(workspace=ws, end_date__isnull=False, end_date__gte=today)
    ending = {f"within_{d}_days": leases.filter(end_date__lte=today + timedelta(days=d)).count() for d in (30, 60, 90)}
    soon = leases.filter(end_date__lte=today + timedelta(days=90)).select_related("property", "landlord")
    return Response(
        {
            "spaces_total": spaces.count(),
            "spaces_by_status": by_status,
            "spaces_by_kind": dict(spaces.values_list("kind").annotate(n=Count("id")).values_list("kind", "n")),
            "leases_ending": ending,
            "leases_ending_soon": [
                {
                    "id": lease.id,
                    "property": str(lease.property),
                    "landlord": str(lease.landlord),
                    "end_date": lease.end_date,
                    "days_left": lease.days_to_expiry(today),
                    "renewal_decision": lease.renewal_decision,
                }
                for lease in soon.order_by("end_date")
            ],
            "payments_pending_review": Payment.objects.active().filter(workspace=ws, status="pending").count(),
            "tickets_open": Ticket.objects.active()
            .filter(workspace=ws)
            .exclude(status__in=["completed", "closed"])
            .aggregate(total=Count("id"), urgent=Count("id", filter=Q(priority="urgent"))),
            "arrivals_next_7_days": Tenancy.objects.active()
            .filter(workspace=ws, status="reserved", start_date__range=(today, today + timedelta(days=7)))
            .count(),
            "departures_next_7_days": Tenancy.objects.active()
            .filter(
                workspace=ws, status__in=["active", "notice_given"], end_date__range=(today, today + timedelta(days=7))
            )
            .count(),
        }
    )


class TenantTicketViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Tenant portal: a tenant sees and opens tickets for the place they live in."""

    serializer_class = s.TenantTicketSerializer
    permission_classes = [IsAuthenticated, HasWorkspace, IsTenant]

    def _person(self):
        person = Person.objects.active().filter(workspace=self.request.workspace, user=self.request.user).first()
        if person is None:
            raise PermissionDenied("No tenant profile is linked to this login.")
        return person

    def get_queryset(self):
        return Ticket.objects.active().filter(workspace=self.request.workspace, reported_by=self._person())

    def perform_create(self, serializer):
        person = self._person()
        live = (
            Tenancy.objects.active()
            .filter(person=person, status__in=["active", "notice_given"])
            .select_related("space")
            .first()
        )
        if live is None:
            raise PermissionDenied("You have no active tenancy.")
        space = serializer.validated_data.get("space") or live.space
        if space.property_id != live.space.property_id:
            raise PermissionDenied("You can only report issues for the property you live in.")
        serializer.save(workspace=self.request.workspace, reported_by=person, property=live.space.property, space=space)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response.data = {"emergency_phone": request.workspace.emergency_phone, "tickets": response.data}
        return response
