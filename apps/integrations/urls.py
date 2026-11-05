from django.urls import path

from . import webhooks

app_name = "hooks"

urlpatterns = [
    path("hostaway/<str:token>/", webhooks.hostaway, name="hostaway"),
    path("email/mailgun/", webhooks.mailgun_email, name="email-mailgun"),
    path("email/postmark/<str:token>/", webhooks.postmark_email, name="email-postmark"),
]
