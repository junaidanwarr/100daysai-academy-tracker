# Student workflow, start to finish

How a student moves through the academy in this system — who does each step,
where in the app it happens, and what changes on its own. It describes what is
built today (Phases 1–3). Sections still to come are listed at the end.

---

## Roles

| Role | Can do |
|---|---|
| **Super Admin** | Everything: batches, enrolment, portal logins, assignments, research review, adding and confirming channels, every student |
| **Instructor** | Their assigned students only: update records and status, review research and assignments, review and confirm channels, work alerts. Cannot enrol students, issue logins or add channels from the console |
| **Management (read-only)** | Sees every student and report; changes nothing |
| **Student** | Their own portal: research, assignments, submitting their channel, roadmap, videos, analytics, notifications |

Everyone can change their own password from the key icon in the top bar, or the
**Change password** link at the foot of the sidebar.

---

## 0. One-time setup (Super Admin)

1. **Settings.** The defaults are:

   | Setting | Default |
   |---|---|
   | Research window | 15 days |
   | Warning before the research deadline | 3 days |
   | Inactive after no activity for | 7 days |
   | Channel expected within | 20 days |
   | First video expected within | 3 days of the channel |
   | No-upload alert after | 10 days |
   | Alert after this many rejections | 2 |

2. **Research → Evaluation criteria** — the scoring rubric:

   | Criterion | Weight | Pass mark | Required |
   |---|---|---|---|
   | Niche clarity | 20 | 60 | Yes |
   | Competitor analysis | 20 | 60 | Yes |
   | Audience definition | 15 | 50 | Yes |
   | Keyword research | 15 | 50 | Yes |
   | Content gap analysis | 15 | 50 | No |
   | Monetization potential | 15 | 50 | Yes |

3. **Batches → New batch** — code, name, dates, instructor. A batch can override
   the research window.
4. **Instructor accounts** — Django admin (`/admin/`) → Users → Add user. A
   password set there is temporary: the instructor must choose their own at
   first sign-in.

## 1. Enrolment (Super Admin)

**Students → Enroll student**:

- name, guardian, email, phone, city, country
- enrolment date, batch, instructor (blank uses the batch instructor)
- course start and expected completion dates
- **research start date** — starts the countdown; the deadline is start plus
  the research window unless one is typed in
- LMS student ID, notes

The system generates the Enrollment ID (e.g. `100DAI-2026-0007`) and sets the
status to **Enrolled**, or **Research in Progress** when a research start date
was given. Both are written to the status history and the audit log.

## 2. Portal login (Super Admin)

1. On the student's page, **Create portal login**.
2. The email and a generated password are shown **once**. Use **Copy for the
   student** and send them privately.
3. At first sign-in the student lands on **Set your own password** and cannot
   open anything else until they do. The new password must be at least 12
   characters, not a common password, not only numbers, not close to their name
   or email, and not the one they were given.
4. A forgotten password: **Reset password** issues a new temporary one, signs
   every old session out, and the student must change it again at sign-in.
5. Any time later, anyone can change their own password; their other devices
   are signed out.

## 3. Niche research (Student, then Instructor)

**Student → My research**: topic, niche and sub-niche, audience and country,
format, video length, upload frequency, competition, earning potential, keyword
research, content gap, monetization notes, and **competitor channels** (link,
subscribers, views, video count, oldest video date, views per video).

- **Save draft** as often as needed, then **Submit for review**. The status
  moves on its own to **Assignment Submitted**.
- Each competitor is checked against four rules: 20–30 videos; subscribers at
  least 0.5% of views; oldest video 2–3 months old; steady growth rather than
  one viral spike.

**Instructor → Research**: score each criterion, write feedback, then decide.

- **Approve** → status moves on its own to **Research Approved**.
- **Request revision** or **Reject** → status moves on its own to **Revision
  Required**, and the student resubmits.

Every attempt is kept as its own version (v1, v2, …). A new version cannot be
submitted while one is waiting for review.

## 4. Assignments (Instructor sets, Student hands in)

1. **Assignments → New assignment** — title, type, batch, due date, maximum
   score, description. Or import a CSV export from the LMS, matched on LMS
   student ID.
2. **Student → My assignments → Hand in** — written response and/or a link.
   Late work is marked late. Another attempt is blocked while one is under
   review or after one is approved.
3. **Record review** — score and feedback; approve or send back. A resubmission
   is attempt 2.

Assignments do **not** change the student's status. Research drives the roadmap.

## 5. The channel (Student submits, Instructor confirms)

### 5a. The student submits it

1. The student creates the channel on YouTube.
2. **My channels → Submit my channel.** The button appears only once research is
   approved.
3. They give the name, **channel link** (required, youtube.com), channel ID
   (optional, starts with `UC`), creation date, Shorts / long-form / mixed,
   language, planned upload schedule and notes.
4. The channel is saved as **Under Review**, with the niche, sub-niche,
   audience, country and format copied from the approved research. A student
   cannot set its status, monetization or ownership-verified flag.
5. A channel ID or link already registered to anyone is refused, so nobody can
   claim another student's channel.
6. The assigned instructor gets an in-app notification; with no instructor,
   every Super Admin does. The "research approved but no channel" alert stops
   for that student.

### 5b. Staff confirm it

1. Open the notification, or **Channels** filtered to **Under Review**. The
   channel page shows an **Awaiting review** banner with a YouTube link.
2. Check the channel belongs to the student and matches the approved niche. Add
   the channel ID if it is missing — nothing can be synced without it.
3. **Edit → status Approved or Active.**
   - The student moves on their own to **Channel Created**, recorded in the
     status history under the staff member's name, and gets a "Channel
     confirmed" notification.
   - This happens only while the student is waiting for a channel (Research
     Approved or Channel Creation Pending). A student further along, inactive,
     at risk or finished is left where they are.
   - Moving an already confirmed channel between Approved, Active and Monetized
     moves no one again.
   - Without approved research, the channel's previous status is kept and a
     warning is shown.

A Super Admin can also add a channel directly (**Channels → Add channel**);
adding it as Approved or Active moves the student the same way. One channel
belongs to one student — linking it to a second needs a Super Admin override
with a written reason.

From Channel Created onwards, staff move the status by hand with **Update
status**: Content Production Started, then Active.

## 6. Tracking the channel (automatic)

- **Public figures** — every 12 hours: subscribers, views, video count, and each
  video's views, likes and comments. Needs the YouTube API key. Runs for every
  channel with a channel ID, including one still under review.
- **Private analytics** — watch time, click-through rate, retention, revenue.
  Staff open the channel → **Connect analytics** → the channel owner signs in
  with Google and consents. Read-only, and can be disconnected at any time.
- Videos appear on their own after each sync; nobody types them in.
- Staff see it on the channel page, **Videos** and **Analytics**; the student on
  **My channels**, **My videos** and **My analytics**. **Sync now** fetches
  immediately.

## 7. Monitoring and alerts (automatic)

A scan runs every 6 hours and raises alerts on the **Alerts** page:

| Alert | Priority | Sent by |
|---|---|---|
| Research deadline approaching | Medium | In-app |
| Research deadline missed | High | In-app, email |
| Research rejected repeatedly | High | In-app, email |
| Assignment not submitted | High | In-app, email |
| Assignment overdue | High | In-app, email |
| Research approved but no channel | Medium | In-app |
| Channel created but no videos | Medium | In-app |
| Upload target missed | Medium | In-app |
| Student inactive | Medium | In-app, email |
| YouTube Analytics disconnected | Low | In-app |
| Final agreement not signed | Medium | In-app, email |

- Daily, a student who is Active or in Content Production with no activity for
  7 days is marked **Inactive**.
- Staff work each alert to a close: In Progress, then Resolved, Ignored or
  Escalated.
- The **activity log** records sign-ins, research, hand-ins, channel
  submissions and videos on its own. Staff log classes attended and mentoring
  sessions with **Log it** on the student's page.

## 8. Day-to-day oversight (staff)

- **Dashboard** — "Needs attention" first, then cohort and research, channels
  and completion, the roadmap-stage chart, upcoming deadlines and recent alerts.
- **Students** — saved filters: needs follow-up, research overdue, due soon,
  rejected, approved but no channel, no uploads, inactive, at risk.
- **Notifications** (bell) — channels waiting for review, alerts.
- **Audit log** — who did what and when, including password changes (never the
  password itself) and channel submissions.

## 9. Completion

Staff set **Batch Completed**, **Dropped Out** or **Suspended**. Batch Completed
and Dropped Out are final; only a Super Admin override moves the student again,
and it is audited.

---

## The roadmap the student sees

Enrollment → Niche Research → Assignment Submission → Assignment Review →
Research Approved → Channel Creation → Content Production → Performance
Monitoring → Batch Completion

**Moves on its own:** research submitted → Assignment Submitted; research
reviewed → Research Approved or Revision Required; channel confirmed by staff →
Channel Created; no activity for the configured window → Inactive.

**Moved by staff** with **Update status**: everything else.

## What needs configuring for the automatic parts

- **Alerts, inactivity flags and YouTube sync** need the scheduler running —
  `qcluster` with `setup_schedules`, or an external scheduler calling the cron
  endpoint on hosts with no persistent process. See
  [DEPLOYMENT.md](DEPLOYMENT.md).
- **Email** needs the Resend API key.
- **YouTube figures** need the YouTube API key; **analytics** need Google OAuth
  credentials.

## Not built yet

Performance scores and Reports (Phase 4); Agreements, Grievances and the signed
completion record (Phase 5). They show a **P4** / **P5** tag in the menu. See
[ROADMAP.md](ROADMAP.md).
