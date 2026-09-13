"""
The portal's jurisdiction boundary.

Two separate gates, and they answer different questions:

  ``student_required``  — is this person a student at all? Staff are redirected
                          to their own console rather than shown a 403, because
                          a Super Admin hitting /portal/ has made a navigation
                          mistake, not a permissions one.

  ``with_student``      — does this student have a tracked record to show?
                          A login can exist before it is linked to a Student
                          row, so every portal page must survive that state
                          instead of raising AttributeError on ``.pk``.

Row visibility is *not* decided here. It stays where it already lives: in the
``for_actor()`` querysets and the service layer, which fail closed. This module
only decides who may reach the portal shell at all.
"""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.core.enums import UserRole


def current_student(actor):
    """The signed-in student's own record, or None. Never accepts an id."""
    student = getattr(actor, "student_profile", None)
    # A soft-deleted record is treated as absent rather than shown read-only:
    # the student should not be looking at a withdrawn enrolment.
    if student and student.deleted_at:
        return None
    return student


def student_required(view):
    """Portal pages are for students. Staff belong in the console."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if getattr(request.user, "role", None) != UserRole.STUDENT:
            return redirect("core:dashboard")
        return view(request, *args, **kwargs)

    return wrapper


def with_student(view):
    """
    Wraps ``student_required`` and passes the student record as the second
    argument. An unlinked login gets an honest explanation, not an error page.
    """

    @wraps(view)
    @student_required
    def wrapper(request, *args, **kwargs):
        student = current_student(request.user)
        if not student:
            return render(request, "portal/unlinked.html", {"active_nav": None})
        return view(request, student, *args, **kwargs)

    return wrapper
