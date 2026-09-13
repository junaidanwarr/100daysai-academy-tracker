# How to test Phases 1–3

Two ways to check the system, and you want both:

1. **The automated suite** — 155 tests, no database or credentials needed.
2. **A manual walkthrough** — sign in and use it, which is the only way to judge
   whether the screens actually say what they should.

Every expected figure below was produced by running these steps against a fresh
seeded database. If yours differ, something has changed.

---

## 0. Setup

Once, from the project root:

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

### Choosing a database

**For a walkthrough, use SQLite** — nothing to install, and the whole app runs:

```bash
python manage.py migrate --settings=config.settings_local
```

Every command below then takes `--settings=config.settings_local`. The database
is a single file, `local.sqlite3`; delete it to start over.

**Before going live, repeat the walkthrough on PostgreSQL**, because that is
what production runs. Point `DB_*` in `.env` at your database, generate the two
secrets as the README describes, and drop the `--settings` flag from every
command.

---

## 1. Run the automated suite

```bash
python manage.py test apps --settings=config.settings_test
```

Expect `Ran 155 tests ... OK`. Two tracebacks scroll past during the run — they
are the deliberate crash-handling and permission-denied tests proving that a
failure is caught and logged. A traceback is only a problem if the final line
is not `OK`.

This covers, without touching the network: the status machine, enrollment ID
generation, weighted scoring, the permission matrix, the alert scan, and the
whole YouTube sync engine against recorded API responses.

---

## 2. Seed and start

```bash
python manage.py seed_demo --settings=config.settings_local
```

This prints four account credentials **once**, to the console. They are never
shown in the application UI. Copy them now.

Expect: 11 settings, 10 alert rules, 13 scoring factors, 6 research criteria,
4 accounts, 2 batches, 20 students.

```bash
python manage.py runserver --settings=config.settings_local
```

Open <http://127.0.0.1:8000/>. Sign in as `admin@100daysai.local`.

> The seeded Super Admin has no authenticator enrolled, so the first sign-in
> goes straight through and prints a note saying so. Enrol TOTP before going
> live — after that, sign-in requires a code from an authenticator app.

---

## 3. Phase 1 — student tracking, alerts, access control

**3.1 Dashboard.** Counts by status, upcoming deadlines, open alerts.

**3.2 Students.** 20 students across all 15 statuses. Search for `Daniyal`,
filter by status, and confirm the overdue students are visibly flagged.

**3.3 Change a status.** Open any student → change status. Only legal next
statuses are offered — the state machine will not let you jump from `ENROLLED`
straight to `BATCH_COMPLETED`. Check the timeline records the change.

**3.4 The configurable research window.** Settings → change *Research window*
from 15 to 10 → save. Then:

```bash
python manage.py shell --settings=config.settings_local -c "from apps.monitoring.jobs import run_named_job; print(run_named_job('recalculate-deadlines'))"
```

Deadlines are re-dated for every student on the default window. Batch
`YTA-2026-02` has a 21-day override and must be untouched — open it and confirm.
Set the window back to 15 afterwards.

**3.5 Alerts.** Run the scan twice:

```bash
python manage.py shell --settings=config.settings_local -c "from apps.monitoring.jobs import run_named_job; print(run_named_job('deadline-scan'))"
```

First run: `created: 12, suppressed: 0, notifications_queued: 22`.
Second run: `created: 0, suppressed: 12`. That suppression is the point — a rule
running hourly must not post the same alert 24 times a day.

**3.6 Notifications.**

```bash
python manage.py shell --settings=config.settings_local -c "from apps.monitoring.jobs import run_named_job; print(run_named_job('dispatch-notifications'))"
```

Expect `processed: 22, sent: 12, skipped: 10`. The 10 skips are email, with
`[email:not-configured]` printed for each — no `RESEND_API_KEY` is set, so the
system records the reason rather than pretending to deliver.

**3.7 Access control.** Sign out, sign in as `instructor1@100daysai.local`:

- The nav no longer offers Audit Logs or Staff.
- Students lists only that instructor's assignees, not all 20.
- Typing `/audit-logs/` in the address bar returns **403**, not a redirect. The
  menu hiding an item is cosmetic; this is the actual boundary.

Then sign in as `manager@100daysai.local` (read-only): everything is visible,
every create and edit button is gone, and posting to an edit URL returns 403.

**3.8 Audit log.** Back as the admin, open Audit Logs. Your status change from
3.4 is there with the old and new values, your email, and your IP.

---

## 4. Phase 2 — research and assignments

**4.1 Criteria.** Research → Criteria. Six criteria with weights totalling 100,
pass marks, and required/evidence flags. Change a weight and save.

**4.2 Evaluate a submission.** Research → open a submitted one. Score each
criterion; the running total updates as you type and must match the total the
server stores when you save. Approve it.

Expect: the score shown as a percentage, an explanation naming the strongest and
weakest criterion, and the student advanced to `RESEARCH_APPROVED` automatically.

**4.3 Request a revision.** On another submission, choose *Request revision* and
give a reason. The reason is mandatory — try saving without one.

**4.4 Resubmission history.** After a revision request, the student's next
submission becomes v2. Open v1 and confirm it still shows
`REVISION_REQUESTED` and its original reason. Nothing is overwritten.

**4.5 Re-reviewing a closed attempt is refused.** Try to review the approved
submission from 4.2 again. It must refuse rather than silently rescore.

**4.6 CSV import.** Assignments → paste into the CSV box, using the column names
shown on the page:

```
lms_student_id,student_email,assignment_id,assignment_title,submitted_at,attempt
,ayesha.khan@example.com,A-1,Niche research,2026-07-01T10:00:00Z,1
GHOST-1,nobody@example.com,A-1,Niche research,2026-07-01T10:00:00Z,1
,bilal.ahmed@example.com,A-1,Niche research,not-a-date,1
```

(Use two real seeded student emails from your Students list.) Expect: the good
rows written, `GHOST-1` reported as unmatched, the malformed date rejected — and
the bad rows must not block the good ones. Import the same content again: it
writes nothing the second time.

**4.7 LMS webhook.** Set `LMS_WEBHOOK_SECRET` in `.env`, restart, then POST to
`/api/webhooks/lms/`. Unsigned and wrongly-signed requests must return **401**;
a correct `X-LMS-Signature` HMAC returns 200; replaying the same payload writes
nothing.

---

## 5. Phase 3 — YouTube

Phase 3 has two halves, and the first is worth testing **before** you have any
Google credentials, because it is where most systems quietly lie.

### 5.1 Without an API key — does it admit what it cannot do?

With `YOUTUBE_API_KEY` unset:

- **Analytics** page: a warning that the key is not configured, and every
  channel row showing what is and is not obtainable.
- **A channel page**: subscribers and views show an em dash — never a `0` —
  with "Last successful sync: Never" stated outright.
- **Data availability** panel: two lists, public and private, each dotted for
  availability, with a sentence explaining exactly why the private ones are not
  reachable.
- The job refuses honestly:

```bash
python manage.py shell --settings=config.settings_local -c "from apps.monitoring.jobs import run_named_job; print(run_named_job('youtube-sync'))"
```

Expect `{'skipped': True, 'reason': 'YOUTUBE_API_KEY is not set, ...'}` — not a
success with zero results.

**The distinction to check:** an em dash means "we cannot obtain this" and a `0`
means "we measured it and it is zero". If you ever see a `0` where the data
cannot exist, that is a bug worth reporting.

### 5.2 With a real API key — public figures

In the Google Cloud console: create a project, enable **YouTube Data API v3**,
create an API key, and put it in `.env` as `YOUTUBE_API_KEY`. Restart the server.

Take any real channel's ID (the `UC…` string, not the `@handle`) — your own is
ideal. Edit a seeded channel, paste it into *YouTube channel ID*, save, then
press **Sync now**.

Expect: real subscriber, view and video counts; up to 50 uploads listed with
Shorts and long-form correctly separated by duration; a sync run recorded as
Success with the quota it spent (3 units for a normal channel).

Worth trying deliberately:

- **A channel that hides its subscriber count** → an em dash, not a zero.
- **A made-up channel ID** → the run is recorded as Failed with "The channel
  could not be found", and no snapshot is written.
- **Press Sync now twice** → videos are not duplicated, but a second snapshot is
  recorded. Snapshots are history; they are never overwritten.

Growth figures need two snapshots at least a day apart, so the growth table will
say "No snapshot from 1 day(s) ago to compare against yet" until tomorrow. That
message appearing is itself the correct behaviour.

### 5.3 With an OAuth client — private analytics

Enable **YouTube Analytics API** on the same project, create an *OAuth client
ID* of type Web application, and add an authorized redirect URI matching
`GOOGLE_OAUTH_REDIRECT_URI` **exactly**, including the trailing slash. For local
testing:

```
http://127.0.0.1:8000/api/oauth/youtube/callback/
```

Put the client ID, secret and that same URI in `.env` and restart. You must be
listed as a test user on the OAuth consent screen while the app is unverified.

On a channel you own, press **Connect analytics**. Check the consent screen
before clicking through: it should state in plain words what will be read, that
access is read-only, that no other Google data is touched, and that you can
disconnect at any time — with a consent version stamped on it.

Then authorize, and press **Sync now**.

Expect: impressions, click-through rate, watch time, average view duration and
percentage viewed populated; traffic sources broken down by percentage on the
video page; and revenue still absent unless you set `YOUTUBE_TRACK_REVENUE`.

Worth trying deliberately:

- **Authorize with the wrong Google account** (one that does not own the
  channel) → refused at connection time, with no grant stored. Discovering this
  months later as empty reports would be much worse.
- **Disconnect** → private metrics stop, but every figure already collected
  stays, because it was true when it was measured. Re-sync and confirm public
  figures still update.
- **Reconnect** → works, and clears the previous revocation.
- **Retention** → open a video and press *Fetch retention*. It is deliberately
  on-demand: it costs one Analytics report per video.

### 5.4 Quota

Set `YOUTUBE_DAILY_QUOTA_UNITS="2"` in `.env`, restart, and run the sync across
several channels. Expect it to stop at the ceiling, mark runs partial, and defer
the rest — never a wall of 403s. Channels are processed oldest-synced-first, so
nothing at the bottom of the list starves. Reset it to `10000` afterwards.

---

## 6. Cleaning up

To start completely fresh, stop the server, delete `local.sqlite3`, and repeat
from step 2. Nothing else on your machine is touched.

If you connected a real Google account during testing, disconnect it from the
channel page, and revoke the app at
<https://myaccount.google.com/permissions> once you are done.
