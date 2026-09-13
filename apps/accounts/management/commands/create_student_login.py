"""
Issues a portal login for an existing student.

Students are tracked whether or not they can sign in, so accounts are created on
request rather than automatically. The password is printed once and is never
shown in the application UI.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.academy.models import Student
from apps.accounts.models import User
from apps.core.crypto import generate_password
from apps.core.enums import UserRole


class Command(BaseCommand):
    help = "Creates a student portal login for an existing Enrollment ID."

    def add_arguments(self, parser):
        parser.add_argument("enrollment_id")
        parser.add_argument("--password", default=None, help="Generated if omitted.")

    def handle(self, *args, **options):
        enrollment_id = options["enrollment_id"]

        student = Student.objects.filter(enrollment_id=enrollment_id).first()
        if not student:
            raise CommandError(f"No student with Enrollment ID {enrollment_id}.")

        if student.user_id:
            self.stdout.write(f"{student.full_name} already has a login ({student.email}).")
            return

        password = options["password"] or generate_password()
        user = User.objects.create_user(
            email=student.email,
            password=password,
            full_name=student.full_name,
            role=UserRole.STUDENT,
        )
        student.user = user
        student.save(update_fields=["user", "updated_at"])

        line = "-" * 56
        self.stdout.write(line)
        self.stdout.write("  STUDENT LOGIN - shown once, here only.")
        self.stdout.write(f"  {student.email}   {password}")
        self.stdout.write(line)
