from datetime import date

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from apps.landlords.models import Landlord, Lease
from apps.people.models import Person
from apps.properties.models import Property, Space
from apps.workspaces.models import Membership, Workspace


@pytest.fixture
def make_workspace(db):
    def _make(slug="oceanway", **kw):
        defaults = dict(
            name=slug.title(),
            slug=slug,
            country="CA",
            base_currency="CAD",
            rule_pack="ca-bc",
            urgent_ticket_emails="admin@example.com, partner@example.com",
            emergency_phone="+1 604 555 0100",
        )
        defaults.update(kw)
        return Workspace.objects.create(**defaults)

    return _make


@pytest.fixture
def ws(make_workspace):
    return make_workspace()


@pytest.fixture
def make_user(db):
    def _make(username, workspace, role):
        user = User.objects.create_user(username=username, password="pass-12345678")
        Membership.objects.create(workspace=workspace, user=user, role=role)
        return user

    return _make


@pytest.fixture
def staff_client(ws, make_user):
    client = APIClient()
    client.force_login(make_user("kimia", ws, "owner"))
    return client


@pytest.fixture
def house(ws):
    prop = Property.objects.create(
        workspace=ws,
        name="Regan St",
        address_line1="1682 Regan St",
        city="Coquitlam",
        region="BC",
        country="CA",
        time_zone="America/Vancouver",
        currency="CAD",
    )
    room = Space.objects.create(workspace=ws, property=prop, kind="room", name="Room 1", monthly_rent=1000, deposit=450)
    bed = Space.objects.create(workspace=ws, property=prop, kind="bed", name="Room 1 bed A", parent=room)
    couch = Space.objects.create(workspace=ws, property=prop, kind="couch", name="Living room couch", nightly_rate=45)
    return {"property": prop, "room": room, "bed": bed, "couch": couch}


@pytest.fixture
def tenant_person(ws):
    return Person.objects.create(workspace=ws, first_name="Sam", last_name="Lee", email="sam@example.com")


@pytest.fixture
def lease(ws, house):
    landlord = Landlord.objects.create(workspace=ws, name="Owner One", phone="+1 604 555 0199")
    return Lease.objects.create(
        workspace=ws,
        property=house["property"],
        landlord=landlord,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        monthly_rent=3200,
        currency="CAD",
    )


@pytest.fixture(autouse=True)
def _plain_static_storage(settings):
    """Tests do not run collectstatic, so skip the hashed-manifest lookup."""
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
