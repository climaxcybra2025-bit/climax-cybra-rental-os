"""REST API authentication with workspace API keys (for n8n and other automation).

Send ``Authorization: Api-Key ros_...``. The request then acts as the key's
user inside the key's workspace, and that user must still be staff there.
"""

from django.utils import timezone
from rest_framework import authentication, exceptions

from apps.records.audit import current_user
from apps.workspaces.models import Membership

from .models import ApiKey


class ApiKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Api-Key"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode(errors="ignore")
        if not header.startswith(self.keyword + " "):
            return None
        key = ApiKey.lookup(header[len(self.keyword) + 1 :].strip())
        if key is None:
            raise exceptions.AuthenticationFailed("Invalid or revoked API key.")
        membership = (
            Membership.objects.select_related("workspace").filter(workspace=key.workspace, user=key.acting_user).first()
        )
        if membership is None or not membership.is_staff_role or not key.acting_user.is_active:
            raise exceptions.AuthenticationFailed("The key's user is no longer staff in this workspace.")
        # The workspace middleware ran before authentication; point the request at the key's workspace.
        request._request.workspace = key.workspace
        request._request.membership = membership
        current_user.set(key.acting_user)  # audit entries name the key's user; the audit middleware resets it
        if not key.last_used_at or (timezone.now() - key.last_used_at).total_seconds() > 300:
            ApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        return key.acting_user, key

    def authenticate_header(self, request):
        return self.keyword
