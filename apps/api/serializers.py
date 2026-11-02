from rest_framework import serializers

from apps.landlords.models import Landlord, LandlordRepresentative, Lease
from apps.operations.models import CleaningDuty, Ticket
from apps.people.models import Person
from apps.properties.models import Property, Space
from apps.records.models import Correspondence, RecordFile
from apps.tenancies.models import Payment, Tenancy


class ScopedSerializer(serializers.ModelSerializer):
    """Workspace is set from the request; related objects must belong to the same workspace."""

    class Meta:
        read_only_fields = ["workspace", "created_at", "updated_at", "archived_at"]

    def validate(self, attrs):
        workspace = self.context["request"].workspace
        for name, value in attrs.items():
            if hasattr(value, "workspace_id") and value.workspace_id != workspace.pk:
                raise serializers.ValidationError({name: "Not found in this workspace."})
        instance = self.Meta.model(**{**attrs, "workspace": workspace}) if not self.instance else self.instance
        if self.instance:
            for k, v in attrs.items():
                setattr(instance, k, v)
        if hasattr(instance, "clean"):
            from django.core.exceptions import ValidationError as DjangoValidationError

            try:
                instance.clean()
            except DjangoValidationError as exc:
                detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
                raise serializers.ValidationError(detail) from exc
        return attrs


def scoped(model_cls, fields="__all__"):
    meta = type(
        "Meta", (), {"model": model_cls, "fields": fields, "read_only_fields": ScopedSerializer.Meta.read_only_fields}
    )
    return type(f"{model_cls.__name__}Serializer", (ScopedSerializer,), {"Meta": meta})


PropertySerializer = scoped(Property)
SpaceSerializer = scoped(Space)
LandlordSerializer = scoped(Landlord)
LandlordRepresentativeSerializer = scoped(LandlordRepresentative)
LeaseSerializer = scoped(Lease)
PersonSerializer = scoped(Person)
TenancySerializer = scoped(Tenancy)
PaymentSerializer = scoped(Payment)
TicketSerializer = scoped(Ticket)
CleaningDutySerializer = scoped(CleaningDuty)
CorrespondenceSerializer = scoped(Correspondence)
RecordFileSerializer = scoped(RecordFile)


class TenantTicketSerializer(serializers.ModelSerializer):
    """What a tenant may set when opening a ticket from the portal."""

    class Meta:
        model = Ticket
        fields = ["id", "space", "category", "priority", "title", "description", "status", "created_at"]
        read_only_fields = ["status", "created_at"]
