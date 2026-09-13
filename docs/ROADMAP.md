# Development Roadmap

The 41-table schema covers all five phases, so later phases are migrations and
interfaces — not rewrites.

---

## Phase 1 — Core student tracking · **delivered**

Custom user model with TOTP MFA for administrators, lockout and middleware
enforcement · role-based access control with row scoping · student profiles with
all 15 statuses and a transition state machine · batch management with a
per-batch research-window override · automated deadline monitoring on a
configurable window · 10 alert rules with deduplication and triage ·
notification dispatch (in-app, email; SMS and WhatsApp adapters) · manual
channel records with all 10 statuses · admin, batch and student dashboards ·
advanced search with 12 saved filters · audit log with field-level diffs ·
student portal · full Django admin.

**Verified running against PostgreSQL:** migrations, seed of 20 students, sign-in
for three roles, alert scan producing 12 alerts then suppressing 12 on rerun,
notification dispatch (12 sent, 10 correctly skipped for the unconfigured email
adapter), research window changed 15 → 10 re-dating 8 students while the 21-day
override batch stayed untouched, student creation generating `100DAI-2026-0021`
with a derived deadline, a status transition recorded in the timeline and audit
log, and 403 responses for an instructor requesting `/audit-logs/` and
`/students/new/` directly. 35 unit tests pass.

---

## Phase 2 — Research and assignment management · **delivered**

Student research submission form (topic, niche, competitors, audience,
keywords, content gaps, monetization potential) with draft support and
repeatable competitor rows · admin criteria editor with weight, pass mark, max
score, required and evidence flags · evaluation screen with per-criterion
scoring and a live weighted total that mirrors the server calculation exactly ·
approve / request revision / reject with the reason recorded · resubmission
creating version N+1 with every prior attempt intact · research approval
advancing the student automatically · LMS adapter layer with manual, CSV and
signed-webhook paths.

**Verified running:** approval scoring 77.15% and propagating the student to
`RESEARCH_APPROVED` with an explained audit entry ("Strongest: Audience
definition (90.0%). Weakest: Keyword research (65.0%)") · re-review of a closed
attempt refused · resubmission producing v2 while v1 kept its
`REVISION_REQUESTED` status and original rejection reason · CSV import of 4 rows
yielding 2 written, `GHOST-1` reported unmatched by identifier and 1 malformed
row that did not block the good ones, with a re-import writing nothing ·
webhook returning 401 unsigned and wrong-signed, 200 with a valid HMAC, and
writing nothing on replay. 65 unit tests pass.

**Outstanding within this phase:** file uploads. `ResearchAttachment` and
`AssignmentFile` are modelled with `FileField`s, but the upload widget and
storage driver are not built.

*Needs from you:* the LMS name, for a concrete vendor adapter. The manual, CSV
and webhook paths work today regardless.

---

## Phase 3 — YouTube integration · **delivered**

Google OAuth consent flow with a plain-language consent screen, signed and
expiring state, encrypted refresh tokens and a student-facing disconnect ·
YouTube Data API for channel statistics, uploads and video metadata · YouTube
Analytics API for impressions, CTR, watch time, AVD, percentage viewed, traffic
sources, retention and revenue, strictly gated on consent · scheduled sync with
daily quota accounting, per-run token refresh and partial-run handling ·
historical snapshots driving daily, weekly and monthly growth · channel, video
and analytics screens that state what is unavailable and why.

**Verified running** against the full suite with every YouTube call served by a
recorded transport: a sync writing a public snapshot and two videos and closing
its run at 3 quota units · a Short classified by its 58-second duration and a
live stream's `P0D` correctly refused a duration · a hidden subscriber count
stored as null while a measured zero comment count stayed zero · analytics rows
written separately from public ones, so an analytics null can never overwrite a
public figure · CTR converted from YouTube's `0.0842` ratio to `8.420` percent ·
traffic sources apportioned 75/25 · a missing impressions report leaving
impressions null without aborting the rest of the sync · a rejected refresh
token marking the grant revoked, keeping the public figures, and reporting the
run PARTIAL rather than FAILED · a quota budget of 1 skipping the uploads call
entirely and recording why · a ceiling of 0 deferring every channel with no HTTP
call made at all · a Google account that does not own the channel refused at
connection time · disconnection succeeding even when Google refuses the
revocation, and keeping every snapshot already collected · the refresh token
absent from the audit log · a second student's channel returning 404 and a
read-only viewer getting 403 on sync and disconnect. 139 tests pass.

**Outstanding within this phase:** the growth screen shows a table, not a chart.
Retention is fetched per video on request rather than on the sweep, which is
deliberate — it costs one Analytics report per video.

*Needs from you:* a Google Cloud project with **YouTube Data API v3** and
**YouTube Analytics API** enabled, an OAuth client whose redirect URI matches
`GOOGLE_OAUTH_REDIRECT_URI` exactly, and — if more than 100 students will
connect — Google verification started early, because review takes weeks.
`YOUTUBE_API_KEY` alone is enough for public figures; the OAuth client is needed
only for the private metrics.

---

## Phase 3.5 — Student portal · **delivered**

The portal became its own app and its own jurisdiction, replacing the two
one-off `/portal/` pages that hung off `academy` and `research`.

- `apps/portal` with its own shell, navigation, access gates and tests
- Nine screens: roadmap, research, assignments, channels, channel detail,
  videos, video detail, analytics, notifications — plus Phase 5 placeholders
  for the completion agreement and the grievance channel
- Every staff-console route now refuses students and redirects them to the
  portal. Previously eight of them returned 200, including the research
  criteria editor with its weightages and pass marks
- Channels are read-only to students; `channel: create/update` was removed from
  the STUDENT matrix, which had contradicted the shipped behaviour
- Fixed: a student submitting their own research raised
  `PermissionDenied: Role STUDENT may not update student` from the status
  propagation in `submit_research`, so portal submission had never worked.
  `change_status(..., propagated=True)` now covers a transition caused by an
  action the calling service already authorized

**Verified:** 202 tests pass. Signed in as a student against SQLite, walked all
nine portal screens, confirmed every console route redirects, and confirmed an
instructor still reaches the whole console and is bounced out of `/portal/`.

---

## Phase 4 — Performance and reporting

- Weighted scoring across all specification section 11 factors
- Bands: Excellent, Satisfactory, Needs Improvement, At Risk, Unsatisfactory
- **Explainable scores** — the stored breakdown shows each factor, its weight,
  the raw value and the points awarded, plus factors excluded because the data
  was unavailable. A score is never a bare number
- Targets per batch, student or channel; expected versus actual with the six
  status indicators
- Report builder across every dimension in section 15
- PDF, Excel, CSV and printable export, each audited

Factors that need analytics are excluded and labelled when consent is absent, so
scoring works for students who never connect their channel.

---

## Phase 5 — Completion and compliance

- Completion document generated from the student's actual record and frozen at
  issue time, so later data changes cannot alter an issued document
- Student, instructor and academy signatures with timestamp, IP and hash
- PDF generation with a content hash for tamper evidence
- Version control: signed documents become read-only; a correction issues
  version N+1 and supersedes rather than edits
- Grievance intake: category, evidence, assignment, response, resolution,
  acknowledgment, appeal
- Communication log: feedback, warnings, meetings, action plans

**Before this goes live:** the completion document must be reviewed by a lawyer
in your jurisdiction. The wording is deliberately neutral and factual and
contains nothing that would discourage a lawful complaint — but drafting it is
not legal advice.
