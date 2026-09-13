"""
Enrollment ID generation (specification 23.1: "Every student must have a unique
Enrollment ID").

Format: PREFIX-YYYY-NNNN, e.g. 100DAI-2026-0042. The sequence restarts each
calendar year, which keeps IDs short and makes the cohort readable at a glance.

Kept free of model imports so the format rules are unit-testable without a
database; the service supplies the current maximum.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PATTERN = re.compile(r"^([A-Z0-9]{2,10})-(\d{4})-(\d{4,})$")


@dataclass(frozen=True)
class ParsedEnrollmentId:
    prefix: str
    year: int
    sequence: int


def format_enrollment_id(prefix: str, year: int, sequence: int) -> str:
    return f"{prefix.upper()}-{year}-{sequence:04d}"


def parse_enrollment_id(value: str) -> ParsedEnrollmentId | None:
    match = PATTERN.match((value or "").strip().upper())
    if not match:
        return None
    return ParsedEnrollmentId(match.group(1), int(match.group(2)), int(match.group(3)))


def is_valid_enrollment_id(value: str) -> bool:
    return parse_enrollment_id(value) is not None


def next_enrollment_id(prefix: str, year: int, highest_existing: str | None) -> str:
    """
    Next ID given the highest existing one for the same prefix and year.
    Passing None (no students yet this year) starts the sequence at 1.
    """
    if not highest_existing:
        return format_enrollment_id(prefix, year, 1)

    parsed = parse_enrollment_id(highest_existing)
    # An unparseable or mismatched previous value must not silently reset the
    # sequence to 1 and collide — start fresh only when the year or prefix
    # genuinely differs.
    if not parsed or parsed.year != year or parsed.prefix != prefix.upper():
        return format_enrollment_id(prefix, year, 1)

    return format_enrollment_id(prefix, year, parsed.sequence + 1)
