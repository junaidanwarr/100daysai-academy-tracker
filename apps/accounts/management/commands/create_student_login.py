"""
Issues a portal login for an existing student.

Students are tracked whether or not they can sign in, so accounts are created on
request rather than automatically. The password is printed once and is never
stored in plain text. Administrators can do the same from the student page.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.academy.models import Student
from apps.accounts.services import StudentLoginError, issue_student_login
from apps.core.context import system_actor


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
            self.stdout.write(f"{student.full_name} already has a login ({student.user.email}).")
            return

        try:
            user, password = issue_student_login(
                system_actor("create_student_login"), student, password=options["password"]
            )
        except StudentLoginError as exc:
            raise CommandError(str(exc)) from exc

        line = "-" * 56
        self.stdout.write(line)
        self.stdout.write("  STUDENT LOGIN - shown once, here only.")
        self.stdout.write(f"  {user.email}   {password}")
        self.stdout.write(line)
