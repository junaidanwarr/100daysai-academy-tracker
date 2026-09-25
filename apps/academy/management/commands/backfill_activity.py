from django.core.management.base import BaseCommand
from django.db import transaction

from apps.academy.backfill import backfill_activity


class Command(BaseCommand):
    help = "Writes activity-log entries for records that predate the log. Safe to re-run."

    @transaction.atomic
    def handle(self, *args, **options):
        written = backfill_activity()
        if not written:
            self.stdout.write("Nothing to backfill; the activity log is already complete.")
            return
        for kind, count in sorted(written.items()):
            self.stdout.write(f"  {count:>5}  {kind}")
        self.stdout.write(f"Wrote {sum(written.values())} activity entries.")
