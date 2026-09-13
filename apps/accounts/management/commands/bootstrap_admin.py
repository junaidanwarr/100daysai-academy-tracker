"""
Create the first Super Admin from environment variables.

Render's free tier has no shell, so `createsuperuser` cannot be run against a
free deployment at all. This command runs unattended on boot instead.

It is deliberately a no-op when the variables are unset, and refuses to touch an
account that already exists: a redeploy must never silently reset the password of
a live administrator account. Delete the two variables from the host once the
account exists.
"""

import os

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Create the first Super Admin from BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD."

    def handle(self, *args, **options):
        email = (os.environ.get("BOOTSTRAP_ADMIN_EMAIL") or "").strip().lower()
        password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD") or ""

        if not email and not password:
            self.stdout.write("bootstrap_admin: not configured, skipping.")
            return

        if not email or not password:
            raise CommandError(
                "BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD must both be set."
            )

        # Short passwords are the common failure here and the account would be
        # the most privileged one in the system, so refuse rather than warn.
        if len(password) < 12:
            raise CommandError("BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters.")

        if User.objects.filter(email=email).exists():
            self.stdout.write(f"bootstrap_admin: {email} already exists, leaving it untouched.")
            return

        User.objects.create_superuser(
            email=email,
            password=password,
            full_name=os.environ.get("BOOTSTRAP_ADMIN_NAME") or "Administrator",
        )
        # The password is never echoed; the operator already has it.
        self.stdout.write(self.style.SUCCESS(f"bootstrap_admin: created Super Admin {email}."))
