"""
The staff console's half of the jurisdiction boundary.

Mirror image of ``apps.portal.access``. A student who reaches a console URL is
sent to their own portal rather than shown a 403: they are not doing anything
forbidden, they are in the wrong building.

This is routing, not authorization. The console's row visibility still comes
from ``for_actor()`` and the service layer, which fail closed on their own — if
this decorator were deleted tomorrow, a student would still see only their own
rows. What they would wrongly see is the console itself, including the scoring
rubric and its weightages, which is what this prevents.
"""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from apps.core.enums import UserRole


def staff_console(view):
    """Restricts a view to the staff console roles."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if getattr(request.user, "role", None) == UserRole.STUDENT:
            return redirect("portal:overview")
        return view(request, *args, **kwargs)

    return wrapper
