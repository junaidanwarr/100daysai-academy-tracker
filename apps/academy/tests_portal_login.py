"""
Issuing portal logins from the student page.

The password is shown once and never stored readable; only an administrator
may issue or reset one; a reset signs the old sessions out.
"""

import re
from datetime import date, timedelta

from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from apps.academy.models import Batch, Student
from apps.accounts.models import User
from apps.accounts.services import MFA_SESSION_KEY
from apps.core.enums import AuditAction, StudentStatus, UserRole
from apps.core.models import AuditLog


def shown_password(response) -> str:
    return re.search(r'id="login-password"[^>]*>([^<]+)<', response.content.decode()).group(1).strip()


class PortalLoginTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@example.test", password="admin-pw-12345", role=UserRole.SUPER_ADMIN, full_name="Admin",
        )
        self.tutor = User.objects.create_user(
            email="tutor@example.test", password="tutor-pw-12345", role=UserRole.INSTRUCTOR, full_name="Tutor",
        )
        batch = Batch.objects.create(code="B-1", name="Batch 1", start_date=date(2026, 1, 1))
        self.student = Student.objects.create(
            enrollment_id="100DAI-2026-0001", full_name="Asha", email="Asha@Example.test",
            enrollment_date=date(2026, 1, 5), batch=batch, instructor=self.tutor, status=StudentStatus.ENROLLED,
        )
        self.url = f"/students/{self.student.pk}/login/"
        self.sign_in_admin()

    def sign_in_admin(self):
        # A Super Admin session is held at the TOTP challenge until it is cleared.
        self.client.force_login(self.admin)
        session = self.client.session
        session[MFA_SESSION_KEY] = True
        session.save()

    def test_the_button_is_offered_to_an_administrator(self):
        self.assertContains(self.client.get(f"/students/{self.student.pk}/"), "Create portal login")

    def test_creating_a_login_shows_a_working_password_once(self):
        response = self.client.post(self.url, {"action": "create"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        password = shown_password(response)

        self.student.refresh_from_db()
        self.assertEqual(self.student.user.email, "asha@example.test")
        self.assertEqual(self.student.user.role, UserRole.STUDENT)

        browser = Client()
        signed_in = browser.post("/login/", {"email": "asha@example.test", "password": password})
        self.assertRedirects(signed_in, "/portal/", fetch_redirect_response=False)
        # An issued password works, but only to reach the page that replaces it.
        self.assertRedirects(browser.get("/portal/"), "/password/", fetch_redirect_response=False)

        # Recorded, but never with the password in it.
        entry = AuditLog.objects.get(action=AuditAction.CREATE, entity_type="User")
        self.assertNotIn(password, str(entry.summary) + str(entry.after) + str(entry.before))

    def test_a_second_create_is_refused_rather_than_making_a_duplicate(self):
        self.client.post(self.url, {"action": "create"})
        response = self.client.post(self.url, {"action": "create"}, follow=True)
        self.assertContains(response, "already has a login")
        self.assertEqual(User.objects.filter(role=UserRole.STUDENT).count(), 1)

    def test_an_email_already_taken_by_another_account_is_refused(self):
        self.student.email = "tutor@example.test"
        self.student.save()
        response = self.client.post(self.url, {"action": "create"}, follow=True)
        self.assertContains(response, "Another account already uses")
        self.student.refresh_from_db()
        self.assertIsNone(self.student.user)

    def test_reset_replaces_the_password_clears_the_lockout_and_signs_out_old_sessions(self):
        old_password = shown_password(self.client.post(self.url, {"action": "create"}))
        user = User.objects.get(email="asha@example.test")
        student_browser = Client()
        student_browser.post("/login/", {"email": user.email, "password": old_password})
        # Signed in: held on the change-password page, not sent to the login form.
        self.assertEqual(student_browser.get("/password/").status_code, 200)

        user.failed_login_count = 5
        user.locked_until = timezone.now() + timedelta(minutes=30)
        user.save()

        new_password = shown_password(self.client.post(self.url, {"action": "reset"}))
        self.assertNotEqual(new_password, old_password)
        user.refresh_from_db()
        self.assertTrue(user.check_password(new_password))
        self.assertIsNone(user.locked_until)
        # The session opened with the old password is gone.
        self.assertRedirects(student_browser.get("/password/"), "/login/?next=/password/", fetch_redirect_response=False)
        self.assertTrue(user.must_change_password)

    def test_an_instructor_may_not_issue_logins(self):
        self.client.force_login(self.tutor)
        self.assertNotContains(self.client.get(f"/students/{self.student.pk}/"), "Create portal login")
        self.assertEqual(self.client.post(self.url, {"action": "create"}).status_code, 403)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.user)

    def test_a_record_linked_to_a_staff_account_cannot_reset_that_password(self):
        self.student.user = self.tutor
        self.student.save()
        response = self.client.post(self.url, {"action": "reset"}, follow=True)
        self.assertContains(response, "staff account")
        self.tutor.refresh_from_db()
        self.assertTrue(self.tutor.check_password("tutor-pw-12345"))

    def test_the_command_line_route_still_works(self):
        call_command("create_student_login", "100DAI-2026-0001", password="cli-pw-123456", stdout=open("/dev/null", "w"))
        self.student.refresh_from_db()
        self.assertTrue(self.student.user.check_password("cli-pw-123456"))
