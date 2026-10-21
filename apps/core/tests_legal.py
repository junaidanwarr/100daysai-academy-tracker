"""The public privacy policy and terms pages Google requires before publishing."""

from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.core.enums import UserRole


class LegalPagesTests(TestCase):
    def test_both_pages_are_public(self):
        for url, heading in (("/privacy/", "Privacy policy"), ("/terms/", "Terms of service")):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertContains(response, f"<h1>{heading}</h1>", html=True)

    def test_privacy_policy_carries_the_statements_google_checks_for(self):
        response = self.client.get("/privacy/")
        for text in (
            "https://www.youtube.com/t/terms",
            "https://policies.google.com/privacy",
            "https://myaccount.google.com/permissions",
            "api-services-user-data-policy",
            "Limited Use",
            "yt-analytics.readonly",
        ):
            self.assertContains(response, text)

    @override_settings(PRIVACY_CONTACT_EMAIL="privacy@academy.test")
    def test_shows_the_contact_address_when_set(self):
        self.assertContains(self.client.get("/privacy/"), "mailto:privacy@academy.test")

    def test_falls_back_to_the_administrator_without_an_address(self):
        self.assertContains(self.client.get("/privacy/"), "Contact your academy administrator")

    def test_a_user_held_on_the_password_page_can_still_read_them(self):
        user = User.objects.create_user(
            email="new@example.test", password="issued-pw-12345", role=UserRole.STUDENT, full_name="New",
        )
        user.must_change_password = True
        user.save(update_fields=["must_change_password"])
        self.client.force_login(user)
        self.assertEqual(self.client.get("/privacy/").status_code, 200)

    def test_linked_from_the_sign_in_page(self):
        response = self.client.get("/login/")
        self.assertContains(response, 'href="/privacy/"')
        self.assertContains(response, 'href="/terms/"')
