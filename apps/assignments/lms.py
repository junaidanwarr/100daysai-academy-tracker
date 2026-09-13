"""
LMS adapter layer.

The academy's LMS has not been named yet, so this ships the seam rather than a
guess: a contract with working manual, CSV and signed-webhook paths. Naming the
platform later adds one adapter class and changes nothing else.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from apps.core.enums import LmsProvider, SubmissionStatus


@dataclass
class LmsStudentRef:
    external_id: str = ""
    email: str | None = None
    full_name: str | None = None


@dataclass
class LmsSubmissionRow:
    external_id: str
    assignment_external_id: str
    student: LmsStudentRef
    # 1-based attempt number as the LMS reports it.
    attempt: int = 1
    submitted_at: datetime | None = None
    status: str = SubmissionStatus.SUBMITTED
    score: float | None = None
    feedback: str | None = None
    rejection_reason: str | None = None
    files: list[dict] = field(default_factory=list)
    # Original payload, retained so a mapping error can be diagnosed.
    raw: dict | None = None


# The many words platforms use for the same state.
STATUS_ALIASES = {
    "NOT_STARTED": SubmissionStatus.NOT_STARTED,
    "NEW": SubmissionStatus.NOT_STARTED,
    "DRAFT": SubmissionStatus.DRAFT,
    "IN_PROGRESS": SubmissionStatus.DRAFT,
    "SUBMITTED": SubmissionStatus.SUBMITTED,
    "TURNED_IN": SubmissionStatus.SUBMITTED,
    "UNDER_REVIEW": SubmissionStatus.UNDER_REVIEW,
    "GRADING": SubmissionStatus.UNDER_REVIEW,
    "APPROVED": SubmissionStatus.APPROVED,
    "PASSED": SubmissionStatus.APPROVED,
    "ACCEPTED": SubmissionStatus.APPROVED,
    "REJECTED": SubmissionStatus.REJECTED,
    "FAILED": SubmissionStatus.REJECTED,
    "REVISION_REQUESTED": SubmissionStatus.REVISION_REQUESTED,
    "RETURNED": SubmissionStatus.REVISION_REQUESTED,
    "NEEDS_REVISION": SubmissionStatus.REVISION_REQUESTED,
    "RESUBMITTED": SubmissionStatus.RESUBMITTED,
    "OVERDUE": SubmissionStatus.OVERDUE,
    "LATE": SubmissionStatus.OVERDUE,
}


def normalise_status(value: str | None) -> str:
    """
    Anything unrecognised becomes SUBMITTED rather than being dropped — a row
    that reaches a human is better than one that silently vanishes.
    """
    key = (value or "").strip().upper().replace(" ", "_").replace("-", "_")
    return STATUS_ALIASES.get(key, SubmissionStatus.SUBMITTED)


CSV_COLUMNS = [
    "lms_student_id",
    "student_email",
    "assignment_id",
    "assignment_title",
    "submitted_at",
    "attempt",
    "status",
    "score",
    "feedback",
    "file_url",
]


def parse_csv(content: str) -> tuple[list[LmsSubmissionRow], list[str]]:
    """
    Parses an export from any LMS. Row-level problems are collected rather than
    raised, so one malformed line does not reject an otherwise good import.
    """
    errors: list[str] = []
    rows: list[LmsSubmissionRow] = []

    reader = csv.DictReader(io.StringIO(content.strip()))
    if not reader.fieldnames:
        return [], ["The file is empty or has no header row."]

    reader.fieldnames = [(name or "").strip().lower().replace(" ", "_") for name in reader.fieldnames]

    for index, row in enumerate(reader, start=2):  # account for the header row
        assignment_id = (row.get("assignment_id") or "").strip()
        student_id = (row.get("lms_student_id") or "").strip()
        email = (row.get("student_email") or "").strip()

        if not assignment_id:
            errors.append(f"Line {index}: missing assignment_id.")
            continue
        if not student_id and not email:
            errors.append(f"Line {index}: needs lms_student_id or student_email to match a student.")
            continue

        raw_attempt = (row.get("attempt") or "").strip()
        attempt = int(raw_attempt) if raw_attempt.isdigit() else 1

        submitted_at = None
        raw_date = (row.get("submitted_at") or "").strip()
        if raw_date:
            parsed = _parse_date(raw_date)
            if parsed is None:
                errors.append(f'Line {index}: submitted_at "{raw_date}" is not a date.')
                continue
            submitted_at = parsed

        raw_score = (row.get("score") or "").strip()
        file_url = (row.get("file_url") or "").strip()

        rows.append(
            LmsSubmissionRow(
                # Deterministic id, so re-importing the same file is idempotent
                # rather than creating duplicate attempts.
                external_id=f"{assignment_id}:{student_id or email}:{attempt}",
                assignment_external_id=assignment_id,
                student=LmsStudentRef(external_id=student_id, email=email or None),
                attempt=attempt,
                submitted_at=submitted_at,
                status=normalise_status(row.get("status")),
                score=float(raw_score) if _is_number(raw_score) else None,
                feedback=(row.get("feedback") or "").strip() or None,
                files=[{"file_name": file_url.rsplit("/", 1)[-1] or "file", "file_url": file_url}] if file_url else [],
                raw=dict(row),
            )
        )

    return rows, errors


def _parse_date(value: str) -> datetime | None:
    from django.utils.dateparse import parse_date, parse_datetime

    parsed = parse_datetime(value)
    if parsed is None:
        as_date = parse_date(value)
        if as_date is None:
            return None
        parsed = datetime.combine(as_date, datetime.min.time())
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


# --- Adapters ---------------------------------------------------------------


class ManualAdapter:
    """Records are entered by staff; there is nothing external to poll."""

    provider = LmsProvider.MANUAL
    label = "Manual entry"

    def is_configured(self) -> bool:
        return True

    def list_submissions(self, since=None) -> list[LmsSubmissionRow]:
        return []


class CsvAdapter:
    provider = LmsProvider.CSV
    label = "CSV import"

    def is_configured(self) -> bool:
        return True

    def list_submissions(self, since=None) -> list[LmsSubmissionRow]:
        return []


class WebhookAdapter:
    provider = LmsProvider.WEBHOOK
    label = "Webhook receiver"

    def is_configured(self) -> bool:
        return bool(settings.LMS_WEBHOOK_SECRET)

    def list_submissions(self, since=None) -> list[LmsSubmissionRow]:
        return []

    def verify(self, raw_body: bytes, signature: str | None) -> bool:
        secret = settings.LMS_WEBHOOK_SECRET
        # An unsigned webhook that mutates student records is worse than one
        # that does not work yet, so a missing secret refuses everything.
        if not secret or not signature:
            return False

        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        provided = signature.strip()
        if provided.lower().startswith("sha256="):
            provided = provided[7:]
        return hmac.compare_digest(expected, provided)

    def parse(self, raw_body: str) -> list[LmsSubmissionRow]:
        payload = json.loads(raw_body)

        if isinstance(payload, list):
            entries = payload
        elif isinstance(payload, dict) and isinstance(payload.get("submissions"), list):
            entries = payload["submissions"]
        else:
            entries = [payload]

        rows = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            external_id = str(entry.get("submission_id") or entry.get("id") or "")
            assignment_id = str(entry.get("assignment_id") or "")
            if not external_id or not assignment_id:
                continue

            raw_date = entry.get("submitted_at")
            rows.append(
                LmsSubmissionRow(
                    external_id=external_id,
                    assignment_external_id=assignment_id,
                    student=LmsStudentRef(
                        external_id=str(entry.get("student_id") or ""),
                        email=entry.get("student_email"),
                        full_name=entry.get("student_name"),
                    ),
                    attempt=int(entry.get("attempt") or 1),
                    submitted_at=_parse_date(str(raw_date)) if raw_date else None,
                    status=normalise_status(entry.get("status")),
                    score=float(entry["score"]) if _is_number(str(entry.get("score", ""))) else None,
                    feedback=entry.get("feedback"),
                    raw=entry,
                )
            )
        return rows


ADAPTERS = {
    LmsProvider.MANUAL: ManualAdapter(),
    LmsProvider.CSV: CsvAdapter(),
    LmsProvider.WEBHOOK: WebhookAdapter(),
}

webhook_adapter = ADAPTERS[LmsProvider.WEBHOOK]


def get_adapter():
    return ADAPTERS.get(settings.LMS_ADAPTER.upper(), ADAPTERS[LmsProvider.MANUAL])
