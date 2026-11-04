from django.urls import path

from . import webhooks

app_name = "hooks"

urlpatterns = [
    path("hostaway/<str:token>/", webhooks.hostaway, name="hostaway"),
]
