from .models import Membership

SESSION_KEY = "workspace_id"


class CurrentWorkspaceMiddleware:
    """Attach ``request.workspace`` and ``request.membership`` for the signed-in user.

    The active workspace is taken from the session when set, otherwise the
    user's first membership. Users with no membership get ``None``.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.workspace = None
        request.membership = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            memberships = Membership.objects.select_related("workspace").filter(user=user, workspace__is_active=True)
            wanted = request.session.get(SESSION_KEY)
            membership = None
            if wanted:
                membership = memberships.filter(workspace_id=wanted).first()
            membership = membership or memberships.order_by("id").first()
            if membership:
                request.membership = membership
                request.workspace = membership.workspace
        return self.get_response(request)
