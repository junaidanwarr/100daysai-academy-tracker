"""Permission matrix and audit redaction tests."""

from django.test import SimpleTestCase

from apps.core.audit import REDACTED_FIELDS, diff_fields, redact
from apps.core.enums import UserRole
from apps.core.permissions import (
    ACTIONS,
    RESOURCES,
    SCOPE_ALL,
    SCOPE_ASSIGNED,
    SCOPE_OWN,
    PermissionDenied,
    assert_can,
    can,
    can_access,
    requires_mfa,
    scope_for,
)


class PermissionMatrixTests(SimpleTestCase):
    def test_super_admin_holds_every_action_on_every_resource(self):
        for resource in RESOURCES:
            for action in ACTIONS:
                self.assertTrue(can(UserRole.SUPER_ADMIN, resource, action), f"{resource}.{action}")
            self.assertEqual(scope_for(UserRole.SUPER_ADMIN, resource), SCOPE_ALL)

    def test_instructors_review_but_never_delete_or_configure(self):
        self.assertTrue(can(UserRole.INSTRUCTOR, "research", "review"))
        self.assertTrue(can(UserRole.INSTRUCTOR, "student", "update"))
        self.assertFalse(can(UserRole.INSTRUCTOR, "student", "delete"))
        self.assertFalse(can(UserRole.INSTRUCTOR, "setting", "configure"))
        self.assertFalse(can(UserRole.INSTRUCTOR, "audit", "read"))
        self.assertFalse(can(UserRole.INSTRUCTOR, "staff", "read"))

    def test_instructors_are_scoped_to_assigned_records(self):
        self.assertEqual(scope_for(UserRole.INSTRUCTOR, "student"), SCOPE_ASSIGNED)
        self.assertEqual(scope_for(UserRole.INSTRUCTOR, "channel"), SCOPE_ASSIGNED)
        self.assertEqual(scope_for(UserRole.INSTRUCTOR, "notification"), SCOPE_OWN)

    def test_students_are_scoped_to_their_own_record_and_cannot_review(self):
        self.assertEqual(scope_for(UserRole.STUDENT, "student"), SCOPE_OWN)
        self.assertTrue(can(UserRole.STUDENT, "research", "create"))
        self.assertFalse(can(UserRole.STUDENT, "research", "review"))
        self.assertFalse(can(UserRole.STUDENT, "student", "update"))
        self.assertFalse(can(UserRole.STUDENT, "alert", "read"))
        self.assertFalse(can(UserRole.STUDENT, "audit", "read"))

    def test_management_is_genuinely_read_only(self):
        self.assertEqual(scope_for(UserRole.MANAGEMENT_READONLY, "student"), SCOPE_ALL)
        self.assertTrue(can(UserRole.MANAGEMENT_READONLY, "student", "read"))
        self.assertTrue(can(UserRole.MANAGEMENT_READONLY, "report", "export"))

        for resource in RESOURCES:
            for action in ("create", "delete", "review", "override", "configure", "sign"):
                self.assertFalse(
                    can(UserRole.MANAGEMENT_READONLY, resource, action), f"{resource}.{action}"
                )

    def test_managements_only_write_is_its_own_notifications(self):
        writable = [r for r in RESOURCES if can(UserRole.MANAGEMENT_READONLY, r, "update")]
        self.assertEqual(writable, ["notification"])

    def test_mfa_is_required_only_for_super_admins(self):
        self.assertTrue(requires_mfa(UserRole.SUPER_ADMIN))
        for role in (UserRole.INSTRUCTOR, UserRole.STUDENT, UserRole.MANAGEMENT_READONLY):
            self.assertFalse(requires_mfa(role))

    def test_navigation_reads_the_same_matrix_as_enforcement(self):
        self.assertFalse(can_access(UserRole.STUDENT, "audit"))
        self.assertFalse(can_access(UserRole.MANAGEMENT_READONLY, "audit"))
        self.assertTrue(can_access(UserRole.SUPER_ADMIN, "audit"))

    def test_guard_raises_a_typed_error(self):
        with self.assertRaises(PermissionDenied):
            assert_can(UserRole.STUDENT, "setting", "configure")
        assert_can(UserRole.SUPER_ADMIN, "setting", "configure")


class AuditTests(SimpleTestCase):
    def test_redacts_every_sensitive_field(self):
        payload = {field: "secret-value" for field in REDACTED_FIELDS}
        payload["full_name"] = "Ayesha Khan"
        result = redact(payload)

        for field in REDACTED_FIELDS:
            self.assertEqual(result[field], "[redacted]")
        self.assertEqual(result["full_name"], "Ayesha Khan")

    def test_diff_reports_only_changed_fields(self):
        before = {"status": "ENROLLED", "full_name": "Ayesha Khan", "city": "Lahore"}
        after = {"status": "ACTIVE", "full_name": "Ayesha Khan", "city": "Lahore"}
        changed_before, changed_after = diff_fields(before, after)

        self.assertEqual(changed_before, {"status": "ENROLLED"})
        self.assertEqual(changed_after, {"status": "ACTIVE"})

    def test_diff_handles_creation_and_deletion(self):
        self.assertEqual(diff_fields(None, {"a": 1}), ({}, {"a": 1}))
        self.assertEqual(diff_fields({"a": 1}, None), ({"a": 1}, {}))
