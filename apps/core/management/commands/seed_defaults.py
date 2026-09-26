"""
Installs the alert rules, research criteria, scoring factors and settings a
deployment needs — and nothing else. No users, no students.

Safe on every deploy: it only adds what is missing and never overwrites a value
an administrator has changed. Run it on a new production database; `seed_demo`
is for walkthroughs only.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.defaults import install_defaults


class Command(BaseCommand):
    help = "Adds missing default alert rules, research criteria, scoring factors and settings."

    @transaction.atomic
    def handle(self, *args, **options):
        added = install_defaults()
        if not any(added.values()):
            self.stdout.write("seed_defaults: everything already present, nothing changed.")
            return
        parts = ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in added.items() if count)
        self.stdout.write(f"seed_defaults: added {parts}.")
