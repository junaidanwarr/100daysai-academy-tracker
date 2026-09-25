"""
The alert scan, end to end against a database.

This exists because the scan is only ever exercised by running it: the rest of
the monitoring tests are pure evaluator logic, and a missing import inside
``run_alert_scan`` slipped through them entirely.

    python manage.py test apps --settings=config.settings_test
"""

from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.academy.models import Batch, Student
from apps.core.enums import AlertPriority, NotificationChannel, StudentStatus
from apps.monitoring.models import Alert, AlertRule
from apps.monitoring.services import run_alert_scan


class AlertScanTests(TestCase):
    def setUp(self):
        self.batch = Batch.objects.create(code="B-1", name="Batch 1", start_date=date(2026, 1, 1))
        AlertRule.objects.create(
            key="research_deadline_missed",
            name="Research deadline missed",
            condition={"evaluator": "RESEARCH_DEADLINE_MISSED", "params": {"grace_days": 0}},
            priority=AlertPriority.HIGH,
            channels=[NotificationChannel.IN_APP],
            cooldown_hours=24,
        )

    def overdue_student(self, name: str) -> Student:
        started = timezone.localdate() - timedelta(days=40)
        return Student.objects.create(
            enrollment_id=f"100DAI-2026-{name}",
            full_name=f"Student {name}",
            email=f"{name}@example.test",
            enrollment_date=started,
            batch=self.batch,
            research_start_date=started,
            research_deadline=started + timedelta(days=15),
            status=StudentStatus.RESEARCH_IN_PROGRESS,
        )

    def test_the_scan_runs_and_raises_an_alert_for_an_overdue_student(self):
        self.overdue_student("0001")

        result = run_alert_scan()

        self.assertEqual(result.rules_evaluated, 1)
        self.assertEqual(result.created, 1)
        self.assertEqual(Alert.objects.count(), 1)

    def test_a_second_scan_suppresses_the_duplicate_rather_than_repeating_it(self):
        # Without deduplication a rule running hourly would post the same alert
        # 24 times a day and staff would stop reading the inbox.
        self.overdue_student("0001")
        run_alert_scan()

        result = run_alert_scan()

        self.assertEqual(result.created, 0)
        self.assertEqual(result.suppressed, 1)
        self.assertEqual(Alert.objects.count(), 1)

    def test_a_student_who_is_not_overdue_raises_nothing(self):
        Student.objects.create(
            enrollment_id="100DAI-2026-0002",
            full_name="On Time",
            email="ontime@example.test",
            enrollment_date=timezone.localdate(),
            batch=self.batch,
            research_start_date=timezone.localdate(),
            research_deadline=timezone.localdate() + timedelta(days=15),
            status=StudentStatus.RESEARCH_IN_PROGRESS,
        )

        result = run_alert_scan()

        self.assertEqual(result.created, 0)
        self.assertEqual(Alert.objects.count(), 0)

    def test_an_inactive_rule_is_not_evaluated(self):
        self.overdue_student("0001")
        AlertRule.objects.update(is_active=False)

        result = run_alert_scan()

        self.assertEqual(result.rules_evaluated, 0)
        self.assertEqual(Alert.objects.count(), 0)


class AssignmentOverdueScanTests(TestCase):
    """One alert per student per overdue assignment; handing in clears the case."""

    def setUp(self):
        from apps.assignments.models import Assignment

        self.batch = Batch.objects.create(code="B-1", name="Batch 1", start_date=date(2026, 1, 1))
        AlertRule.objects.create(
            key="assignment_overdue",
            name="Assignment overdue",
            condition={"evaluator": "ASSIGNMENT_OVERDUE", "params": {"grace_days": 0}},
            priority=AlertPriority.HIGH,
            channels=[NotificationChannel.IN_APP],
            cooldown_hours=24,
        )
        self.students = [
            Student.objects.create(
                enrollment_id=f"100DAI-2026-000{n}", full_name=f"Student {n}", email=f"{n}@example.test",
                enrollment_date=date(2026, 1, 5), batch=self.batch, status=StudentStatus.ACTIVE,
            )
            for n in (1, 2)
        ]
        self.overdue = Assignment.objects.create(
            title="Branding kit", batch=self.batch, due_at=timezone.now() - timedelta(days=2)
        )
        Assignment.objects.create(title="Not due yet", batch=self.batch, due_at=timezone.now() + timedelta(days=2))

    def test_only_students_with_nothing_in_are_flagged(self):
        from apps.assignments.models import AssignmentSubmission
        from apps.core.enums import SubmissionStatus

        AssignmentSubmission.objects.create(
            assignment=self.overdue, student=self.students[0], version=1,
            status=SubmissionStatus.SUBMITTED, submitted_at=timezone.now(),
        )
        result = run_alert_scan()
        self.assertEqual(result.created, 1)
        alert = Alert.objects.get()
        self.assertEqual(alert.student, self.students[1])
        self.assertIn("Branding kit", alert.problem)

        self.assertEqual(run_alert_scan().created, 0)
