"""View helpers that enforce the permission matrix."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

from apps.core.permissions import can


class PermissionRequiredMixin(LoginRequiredMixin):
    """
    Checks the application permission matrix, not Django's own permission
    system. Set ``resource`` and ``action`` on the view.

    Raising rather than redirecting is deliberate: a user who reaches a URL
    they may not see gets a 403, not a silent bounce that hides the boundary.
    """

    resource: str = ""
    action: str = "read"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not can(request.user.role, self.resource, self.action):
            raise DjangoPermissionDenied(
                f"Your role ({request.user.get_role_display()}) may not {self.action} {self.resource}."
            )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = self.request.user.role
        # Templates ask "may I?" through this rather than re-deriving rules.
        context["perms_matrix"] = {
            resource: {action: can(role, resource, action) for action in ("read", "create", "update", "delete", "review", "export", "configure", "override")}
            for resource in ("student", "batch", "research", "assignment", "channel", "alert", "setting", "audit", "staff", "report")
        }
        return context
