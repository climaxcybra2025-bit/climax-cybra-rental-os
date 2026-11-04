from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("properties", views.PropertyViewSet, basename="property")
router.register("spaces", views.SpaceViewSet, basename="space")
router.register("landlords", views.LandlordViewSet, basename="landlord")
router.register("landlord-representatives", views.LandlordRepresentativeViewSet, basename="landlordrepresentative")
router.register("leases", views.LeaseViewSet, basename="lease")
router.register("people", views.PersonViewSet, basename="person")
router.register("tenancies", views.TenancyViewSet, basename="tenancy")
router.register("payments", views.PaymentViewSet, basename="payment")
router.register("tickets", views.TicketViewSet, basename="ticket")
router.register("cleaning-duties", views.CleaningDutyViewSet, basename="cleaningduty")
router.register("correspondence", views.CorrespondenceViewSet, basename="correspondence")
router.register("files", views.RecordFileViewSet, basename="recordfile")
router.register("listings", views.ListingViewSet, basename="listing")
router.register("listing-channels", views.ListingChannelViewSet, basename="listingchannel")
router.register("portal/tickets", views.TenantTicketViewSet, basename="portal-ticket")

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("", include(router.urls)),
]
