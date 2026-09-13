"""
Registers the default django-q2 schedules.

Idempotent: running it again updates the existing schedules rather than
duplicating them.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django_q.models import Schedule

from apps.monitoring.jobs import JOB_SCHEDULES

# Maps our (type, minutes) tuples onto django-q2's schedule vocabulary.
TYPE_MAP = {
    "I": Schedule.MINUTES,
    "H": Schedule.HOURLY,
    "D": Schedule.DAILY,
    "W": Schedule.WEEKLY,
}


class Command(BaseCommand):
    help = "Creates or updates the scheduled background jobs."

    def handle(self, *args, **options):
        now = timezone.now()

        for job, (kind, every) in JOB_SCHEDULES.items():
            schedule_type = TYPE_MAP[kind]

            defaults = {
                "func": "apps.monitoring.jobs.run_named_job",
                "args": f"'{job}'",
                "schedule_type": schedule_type,
                "repeats": -1,
                "next_run": now,
            }
            # MINUTES is the only type that also needs a `minutes` value; the
            # hourly/daily/weekly types carry their own interval.
            if schedule_type == Schedule.MINUTES:
                defaults["minutes"] = every

            Schedule.objects.update_or_create(name=f"academy:{job}", defaults=defaults)
            self.stdout.write(f"  ok  {job} ({kind}/{every})")

        self.stdout.write(self.style.SUCCESS(f"\nRegistered {len(JOB_SCHEDULES)} schedules."))
        self.stdout.write("Start the worker with: python manage.py qcluster")
