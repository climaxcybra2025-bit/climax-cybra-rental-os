from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.PortalHomeView.as_view(), name="home"),
    path("tickets/new/", views.PortalTicketCreateView.as_view(), name="ticket-create"),
    path("tickets/<int:pk>/", views.PortalTicketDetailView.as_view(), name="ticket"),
    path("payments/new/", views.PortalPaymentProofView.as_view(), name="payment-proof"),
]
