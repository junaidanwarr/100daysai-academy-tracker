"""
Regression tests for the system actor.

An earlier version fabricated an unsaved ``User`` for webhook ingestion. Django
assigns the UUID default the moment a model is constructed, so it looked
persisted to anything checking ``pk`` — and the audit write then violated the
foreign key to ``users``. These tests pin the contract that prevents a repeat.
"""

from django.test import SimpleTestCase

from apps.core.context import SystemActor, system_actor
from apps.core.enums import UserRole
from apps.core.permissions import assert_can, can


class SystemActorTests(SimpleTestCase):
    def test_has_no_primary_key(self):
        # The bug: a truthy pk made write_audit attach a non-existent FK.
        actor = system_actor("alert-scan")
        self.assertIsNone(actor.pk)
        self.assertFalse(getattr(actor, "pk", None))

    def test_is_not_a_user_model_instance(self):
        from apps.accounts.models import User

        self.assertNotIsInstance(system_actor(), User)
        self.assertIsInstance(system_actor(), SystemActor)

    def test_carries_a_named_identity_for_the_audit_trail(self):
        actor = system_actor("lms-webhook")
        self.assertEqual(actor.email, "lms-webhook@internal")
        self.assertEqual(actor.full_name, "lms-webhook")
        self.assertEqual(str(actor), "lms-webhook@internal")

    def test_satisfies_the_permission_interface(self):
        actor = system_actor()
        self.assertEqual(actor.role, UserRole.SUPER_ADMIN)
        self.assertTrue(can(actor.role, "assignment", "create"))
        assert_can(actor.role, "student", "update")  # must not raise

    def test_is_not_treated_as_a_signed_in_user(self):
        self.assertFalse(system_actor().is_authenticated)
