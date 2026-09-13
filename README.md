# 100DaysAI YouTube Academy Tracking System

**Django 6 · PostgreSQL · django-q2 · no Node toolchain**

Tracks every student from enrollment to batch completion: research deadlines,
assignment submissions and resubmissions, YouTube channel creation and
performance, alerts, and a signed completion record.

**Phases 1, 2 and 3 are built and verified running.** Phases 4 and 5 are scoped
in [docs/ROADMAP.md](docs/ROADMAP.md); the 40-table schema already covers all
five, so later phases are migrations rather than rewrites.

To check it yourself, follow [docs/TESTING.md](docs/TESTING.md) — it runs the
suite and walks all three phases, including what should happen *before* you have
any Google credentials.

---

## Quick start

```bash
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then generate the two required secrets:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print('SECRET_KEY=' + k())"
```

```bash
python -c "import os,base64;print('ENCRYPTION_KEY=' + base64.b64encode(os.urandom(32)).decode())"
```

Point `DB_*` at your PostgreSQL (Neon, Supabase, or your own), then:

```bash
python manage.py migrate && python manage.py seed_demo && python manage.py runserver
```

The seed prints four account credentials to the console, once. They are never
shown in the application UI. Change them at first sign-in.

### Background jobs

Deadline scanning, alert generation and notification delivery run in a worker:

```bash
python manage.py qcluster
```

Register the default schedules once:

```bash
python manage.py setup_schedules
```

If your host has no persistent process, point a scheduler at the cron endpoint
instead — same handlers, no code change:

```bash
curl -X POST -H "Authorization: Bearer $CRON_SECRET" https://your-domain/api/cron/deadline-scan/
```

Jobs: `deadline-scan`, `recalculate-deadlines`, `dispatch-notifications`,
`flag-inactive`, `prune-sessions`, `youtube-sync`.

### YouTube synchronisation

`youtube-sync` runs every 12 hours and syncs the channels whose configured
cadence says they are due, so changing **Settings → YouTube sync cadence**
(every 6 hours, every 12 hours, daily, weekly, or manual) takes effect without
rescheduling anything.

Channels are processed oldest-synced-first and each run stops at
`YOUTUBE_DAILY_QUOTA_UNITS`, marking itself partial rather than producing a wall
of 403s — so a large cohort is covered over successive cycles instead of
starving the last channels in the list. Every attempt writes a `SyncJobRun` with
the quota it spent and the error if it failed, which is why the channel page can
say "last successful sync: never" as a fact rather than leaving a blank.

Without `YOUTUBE_API_KEY` the job reports `{"skipped": true, "reason": ...}`
rather than claiming to have synced.

---

## Layout

```
config/            settings, urls, wsgi/asgi
apps/
  core/            enums, audit, permissions, settings service, dashboard, cron
  accounts/        custom User, TOTP MFA, sign-in and lockout
  academy/         batches, students, status machine, saved filters
  research/        criteria sets, versioned submissions, scores
  assignments/     assignments, versioned attempts, LMS connections
  youtube/         channels, OAuth grants, videos, analytics snapshots
                   api.py sync clients · oauth.py consent · services.py sync engine
  monitoring/      alert rules, evaluators, notifications, jobs, performance
  completion/      completion records, signatures, grievances, communication
  portal/          the student-facing site: its own nav, shell and access rules
templates/         Django templates
static/css/        hand-written CSS, no build step
```

---

## Three rules that shape the codebase

**Nothing reaches the ORM except through a service.** Views call
`apps/*/services.py`; every service takes the acting user, which carries who is
acting, what they may do, which rows they may see, and what to attribute in the
audit log.

**Middleware is not the security boundary.** `MfaEnforcementMiddleware` blocks a
Super Admin who has not cleared the TOTP challenge, but every authorization
decision is made in the service layer and in the queryset `for_actor()` scoping.

**The student portal and the staff console are separate jurisdictions, not two
sizes of the same one.** Everything under `/portal/` is the student's; everything
else is the console's. `apps.portal.access.student_required` and
`apps.core.access.staff_console` are the two gates, and they redirect rather than
403, because someone in the wrong building has made a navigation mistake.

This is deliberately not the same thing as row scoping. A student holds `read`
on `student` and `research` — scoped to their own rows — so filtering the staff
menu by the permission matrix happily offered them `/students/` and the rubric
editor. Row scoping meant they saw only their own data there, but the scoring
criteria and their weightages are not row-scoped, and a student who knows the
weightings knows exactly where to spend effort to pass. Hence a separate menu
(`apps/portal/navigation.py`) rather than a filtered one, and a hard gate on
every console view. `apps/portal/tests.py` walks the whole boundary from both
sides.

---

## Two things to know

**Public and private YouTube data are not the same.** Subscribers, views, video
counts and upload dates come from the public Data API with only a channel ID and
`YOUTUBE_API_KEY`. Impressions, click-through rate, average view duration,
audience retention, traffic sources and revenue require each student to
authorize access to their own YouTube Analytics — there is no other lawful
route. Analytics-only columns are nullable and written solely by
`ANALYTICS_API` snapshot rows, so the system shows an em dash and a stated
reason for anything it cannot obtain, never a zero.

Consent is asked for on a screen that says in plain words what will be read and
what cannot be touched, stamped with a version so it is always possible to show
what a student agreed to. The refresh token is stored encrypted and nothing
else; access tokens are exchanged per sync and never persisted. Disconnecting
revokes at Google, stops private metrics immediately, and keeps the snapshots
already collected as a record of what was true at the time.

**The completion document is not legal advice.** It is drafted as a neutral
factual record with acknowledgment, code of conduct, grievance and
dispute-resolution clauses, and contains nothing discouraging a lawful
complaint. Have a lawyer in your jurisdiction review it before treating it as
binding.

---

## Commands

| Command | Does |
|---|---|
| `python manage.py runserver` | Development server |
| `python manage.py qcluster` | Background job worker |
| `python manage.py setup_schedules` | Register the default job schedules |
| `python manage.py seed_demo` | Seed demonstration data |
| `python manage.py test apps --settings=config.settings_test` | Run the test suite (in-memory SQLite; no database to provision) |
| `... --settings=config.settings_local` | Run any command against a local SQLite file instead of PostgreSQL, for exploring the app without provisioning a database |
| `python manage.py createsuperuser` | Create an admin account |
| `python manage.py create_student_login <ENROLLMENT_ID>` | Issue a portal login for a student |
| `python manage.py check --deploy` | Production readiness check |

The Django admin at `/admin/` covers every model, with the audit log, signatures
and analytics snapshots registered read-only because they are append-only by
design.
