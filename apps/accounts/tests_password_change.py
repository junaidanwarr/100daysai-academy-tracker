"""
Changing your own password, and being made to when someone else chose it.

Every role can change its own password. A password that was issued — by the
student page, the Django admin, the demo seed or the bootstrap command — holds
the account on the change page until the user picks their own.
"""

from django.test import Client, TestCase

from apps.accounts.admin import IssuedPasswordChangeForm, IssuedPasswordCreationForm
from apps.accounts.models import User
from apps.accounts.services import MFA_SESSION_KEY
from apps.core.enums import AuditAction, UserRole
from apps.core.models import AuditLog

OLD = "issued-Pass-2026x"
NEW = "my-own-Secret-91z"


def change(client, old=OLD, new=NEW, confirm=None):
    return client.post("/password/", {
        "old_password": old, "new_password1": new, "new_password2": confirm if confirm is not None else new,
    })


class VoluntaryPasswordChangeTests(TestCase):
    def setUp(self):
        self.tutor = User.objects.create_user(
            email="tutor@example.test", password=OLD, role=UserRole.INSTRUCTOR, full_name="Zoya Rahman",
        )
        self.client.post("/login/", {"email": self.tutor.email, "password": OLD})

    def test_the_page_is_linked_from_every_signed_in_page(self):
        self.assertContains(self.client.get("/dashboard/"), 'href="/password/"')

    def test_the_normal_page_keeps_the_navigation(self):
        response = self.client.get("/password/")
        self.assertTemplateUsed(response, "accounts/password_change.html")
        self.assertContains(response, 'class="sidebar"')

    def test_a_valid_change_sets_the_new_password_and_keeps_this_session(self):
        response = change(self.client)
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        self.tutor.refresh_from_db()
        self.assertTrue(self.tutor.check_password(NEW))
        self.assertIsNotNone(self.tutor.password_changed_at)
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)

    def test_other_sessions_are_signed_out(self):
        other_device = Client()
        other_device.post("/login/", {"email": self.tutor.email, "password": OLD})
        self.assertEqual(other_device.get("/dashboard/").status_code, 200)

        change(self.client)
        self.assertRedirects(other_device.get("/dashboard/"), "/login/?next=/dashboard/", fetch_redirect_response=False)

    def test_a_wrong_current_password_changes_nothing(self):
        response = change(self.client, old="not-the-password")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "incorrectly")
        self.tutor.refresh_from_db()
        self.assertTrue(self.tutor.check_password(OLD))

    def test_the_site_password_rules_apply(self):
        for weak in ("short1A", "123456789012", "password1234"):
            with self.subTest(weak=weak):
                self.assertEqual(change(self.client, new=weak).status_code, 200)
        self.assertEqual(change(self.client, confirm="something-else-entirely").status_code, 200)
        self.tutor.refresh_from_db()
        self.assertTrue(self.tutor.check_password(OLD))

    def test_reusing_the_current_password_is_refused(self):
        response = change(self.client, new=OLD)
        self.assertContains(response, "different from your current one")

    def test_the_change_is_audited_without_the_password(self):
        change(self.client)
        entry = AuditLog.objects.get(action=AuditAction.UPDATE, entity_type="User", entity_id=self.tutor.pk)
        self.assertIn("changed their password", entry.summary)
        recorded = str(entry.summary) + str(entry.before) + str(entry.after)
        self.assertNotIn(NEW, recorded)
        self.assertNotIn(OLD, recorded)

    def test_signed_out_visitors_are_sent_to_sign_in(self):
        self.assertRedirects(Client().get("/password/"), "/login/?next=/password/", fetch_redirect_response=False)


class ForcedPasswordChangeTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            email="asha@example.test", password=OLD, role=UserRole.STUDENT, full_name="Asha",
            must_change_password=True,
        )
        self.client.post("/login/", {"email": self.student.email, "password": OLD})

    def test_every_other_page_redirects_to_the_change_page(self):
        for path in ("/portal/", "/portal/research/", "/dashboard/", "/students/", "/admin/"):
            with self.subTest(path=path):
                self.assertRedirects(self.client.get(path), "/password/", fetch_redirect_response=False)

    def test_the_forced_page_has_no_navigation_but_can_sign_out(self):
        response = self.client.get("/password/")
        self.assertTemplateUsed(response, "accounts/password_change_forced.html")
        self.assertNotContains(response, 'class="sidebar"')
        self.assertContains(response, "Sign out instead")
        self.assertRedirects(self.client.post("/logout/"), "/login/", fetch_redirect_response=False)

    def test_the_issued_password_cannot_be_kept(self):
        change(self.client, new=OLD)
        self.student.refresh_from_db()
        self.assertTrue(self.student.must_change_password)

    def test_changing_it_releases_the_account(self):
        self.assertRedirects(change(self.client), "/portal/", fetch_redirect_response=False)
        self.student.refresh_from_db()
        self.assertFalse(self.student.must_change_password)
        self.assertEqual(self.client.get("/portal/").status_code, 200)
        entry = AuditLog.objects.get(action=AuditAction.UPDATE, entity_type="User", entity_id=self.student.pk)
        self.assertIn("required after an issued password", entry.summary)


class ForcedChangeAfterMfaTests(TestCase):
    def test_a_super_admin_clears_the_second_factor_before_choosing_a_password(self):
        admin = User.objects.create_user(
            email="admin@example.test", password=OLD, role=UserRole.SUPER_ADMIN, full_name="Admin",
            must_change_password=True,
        )
        self.client.force_login(admin)
        session = self.client.session
        session[MFA_SESSION_KEY] = False
        session.save()
        self.assertRedirects(self.client.get("/password/"), "/mfa/", fetch_redirect_response=False)

        session = self.client.session
        session[MFA_SESSION_KEY] = True
        session.save()
        self.assertRedirects(self.client.get("/dashboard/"), "/password/", fetch_redirect_response=False)


class IssuedPasswordTests(TestCase):
    """Every way a password gets set for someone else flags the account."""

    def test_admin_created_accounts_must_change_their_password(self):
        form = IssuedPasswordCreationForm(data={
            "email": "new@example.test", "full_name": "New Person", "role": UserRole.INSTRUCTOR,
            "password1": OLD, "password2": OLD,
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.save().must_change_password)

    def test_an_admin_password_reset_flags_the_account(self):
        user = User.objects.create_user(email="t@example.test", password=OLD, role=UserRole.INSTRUCTOR, full_name="T")
        form = IssuedPasswordChangeForm(user, data={"password1": NEW, "password2": NEW})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        user.refresh_from_db()
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.check_password(NEW))

    def test_existing_accounts_are_not_flagged_by_default(self):
        user = User.objects.create_user(email="x@example.test", password=OLD, full_name="X")
        self.assertFalse(user.must_change_password)
