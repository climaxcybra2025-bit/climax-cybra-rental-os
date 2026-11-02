from rest_framework.permissions import BasePermission


class HasWorkspace(BasePermission):
    """The user must belong to an active workspace."""

    message = "You are not a member of an active workspace."

    def has_permission(self, request, view):
        return getattr(request, "workspace", None) is not None


class IsWorkspaceStaff(BasePermission):
    """Owner, admin, partner or team member of the current workspace."""

    def has_permission(self, request, view):
        membership = getattr(request, "membership", None)
        return membership is not None and membership.is_staff_role


class IsTenant(BasePermission):
    def has_permission(self, request, view):
        membership = getattr(request, "membership", None)
        return membership is not None and membership.role == "tenant"
