# Deployment

The system is two processes, and **both are required**:

| Process | Command | What breaks without it |
|---|---|---|
| **web** | `gunicorn config.wsgi:application` | Everything |
| **worker** | `python manage.py qcluster` | Pages still load, but no deadline is ever checked, no alert ever fires, no notification is ever delivered, and YouTube never syncs |

A host that cannot run a second long-lived process is not suitable unless you
drive the jobs over HTTP instead — see *Hosts without a worker* at the end.

---

## 1. Before you deploy anything

### Generate real secrets

The `.env` in this repository holds development placeholders. Production needs
fresh values:

```bash
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(64))"
```

```bash
python -c "import os,base64; print('ENCRYPTION_KEY=' + base64.b64encode(os.urandom(32)).decode())"
```

```bash
python -c "import secrets; print('CRON_SECRET=' + secrets.token_urlsafe(32))"
```

> **`ENCRYPTION_KEY` deserves particular care.** It decrypts every stored
> YouTube OAuth refresh token and every TOTP secret. Lose it and every student
> has to reconnect their channel and every administrator has to re-enrol their
> authenticator. Change it and the same thing happens — the old ciphertext
> becomes unreadable. Keep it in your host's secret store *and* in a password
> manager, and never rotate it casually.

### Required environment variables

| Variable | Value |
|---|---|
| `SECRET_KEY` | from above |
| `ENCRYPTION_KEY` | from above |
| `CRON_SECRET` | from above |
| `DEBUG` | `false` |
| `ALLOWED_HOSTS` | `tracker.yourdomain.com` |
| `CSRF_TRUSTED_ORIGINS` | `https://tracker.yourdomain.com` |
| `DB_NAME` `DB_USER` `DB_PASSWORD` `DB_HOST` `DB_PORT` | from your Postgres provider |
| `DB_SSLMODE` | `require` for any managed Postgres |
| `DB_POOLED` | `true` if your provider gives you a *pooled* connection string |

Everything else has a working default. Integrations (YouTube, email, SMS,
WhatsApp, LMS) stay switched off until you supply their keys, and the Settings
page shows which are configured.

### Do not seed demonstration data

`manage.py seed_demo` creates twenty fictional students. It exists for
walkthroughs. **Never run it against production.** Create your first real
administrator with `createsuperuser` instead.

---

## 2. Render

`render.yaml` in the project root defines all three resources, so most of this
is filling in values rather than clicking through forms.

### What it will cost

Be clear about this before you start, because Render's free tier cannot run
this system properly:

| Resource | Free | Why it matters |
|---|---|---|
| Web service | Yes | Spins down after 15 minutes idle; the next visitor waits ~30 seconds |
| PostgreSQL | 30 days only | **Render deletes free databases after 30 days.** Fine for evaluation, not for real student records |
| Worker | **No** | ~$7/month. Without it nothing is monitored at all |

So: free is fine to *try* it. For real use, budget roughly **$14/month** (worker
plus a paid database). If the worker cost is the blocker, section 5 shows how
to drive the same jobs from a free external scheduler.

### a. Put the project in Git

```bash
git init && git add -A && git commit -m "100DaysAI Academy Tracker"
```

Check nothing sensitive is staged — `.gitignore` already excludes `.env`,
`.venv/`, `wheels/` and the local SQLite file:

```bash
git status --porcelain | grep -E "\.env$|\.venv|wheels/|sqlite3" || echo "clean"
```

Create an empty repository on GitHub, then push to it.

### b. Generate your ENCRYPTION_KEY now

Render can generate `SECRET_KEY` and `CRON_SECRET` itself, but `ENCRYPTION_KEY`
must be exactly 32 random bytes in base64, so you supply it:

```bash
python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
```

Save it in a password manager before going further. It decrypts every stored
YouTube token and TOTP secret; losing it means every student reconnects and
every administrator re-enrols.

### c. Create the Blueprint

On render.com: *New* → *Blueprint* → connect your repository. Render reads
`render.yaml` and proposes the database, web service and worker.

It will prompt for the values marked `sync: false`:

- `ENCRYPTION_KEY` — from step b, the same value for both services
- `ALLOWED_HOSTS` — leave as `academy-web.onrender.com` for now
- `CSRF_TRUSTED_ORIGINS` — `https://academy-web.onrender.com`

If you are evaluating on free plans, delete the `academy-worker` block from
`render.yaml` before applying, and use section 5 instead.

### d. First deploy

Render builds, runs `collectstatic`, and starts both services. The first build
takes a few minutes.

### e. Fix the hostname if it differs

Render may append a suffix to the service name. Once the URL is visible in the
dashboard, set `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` to match it exactly,
then redeploy. A mismatch shows as a blank `400 Bad Request` with no
explanation — it is the most common first-deploy failure.

### f. Migrate and create your administrator

Render's free plan has no pre-deploy hook, so run migrations from the service
*Shell* tab:

```bash
python manage.py migrate --noinput
```

```bash
python manage.py setup_schedules
```

```bash
python manage.py createsuperuser
```

On a paid instance you can instead add `preDeployCommand: python manage.py
migrate --noinput && python manage.py setup_schedules` to the web service in
`render.yaml`, and it runs automatically on every deploy.

---

## 2a. Render free tier — no credit card

Render asks for card verification to create a **Blueprint** and to create a
**PostgreSQL instance**, even on the free plans. It does not ask when you create
a free **web service** by hand. So the free route is to create the web service
manually and host the database on Neon, which asks for nothing.

This is not purely a workaround. Render deletes a free Postgres database after
30 days; Neon's free project does not expire.

**1. Database.** Sign up at neon.tech with GitHub, create a project, copy the
connection string. The copy button gives you the *pooled* endpoint — the
hostname contains `-pooler`. PgBouncer cannot hold server-side prepared
statements and psycopg3 uses them by default, so with a pooled endpoint you must
also set `DB_POOLED=true`. Either use the direct endpoint, or set that variable;
both work, and pooled is the better choice on a free web instance that sleeps.

**2. Migrate from your own machine first**, so a bad connection string fails
somewhere legible instead of inside a build log:

```
$env:DATABASE_URL="<the Neon string>"; .\.venv\Scripts\python.exe manage.py migrate
```

**3. Web service.** New -> Web Service -> connect the repository -> plan Free.
Build and start commands are the ones in `render.yaml`; copy the long
`startCommand` verbatim, as it carries `migrate`, `bootstrap_admin` and
`setup_schedules`, which a free instance has no shell to run any other way.

Set `DATABASE_URL` to the Neon string rather than linking a Render database.
Everything else is as section 2.

**What you give up.** No worker, so no job runs by itself — see section 5. The
web service sleeps after 15 minutes idle and takes about 50 seconds to wake.
Neither is a fault to debug.

## 2b. Railway — alternative

Managed Postgres, reads the `Procfile`, and runs a worker as a second service.
Roughly $5–10/month for an academy-sized deployment.

**a. Put the project in Git.** From the project root:

```bash
git init && git add -A && git commit -m "100DaysAI Academy Tracker"
```

The `.gitignore` already excludes `.env`, `.venv/`, `wheels/` and
`local.sqlite3`. Confirm nothing sensitive is staged before pushing:

```bash
git status --porcelain | grep -E "\.env$|\.venv|wheels/|sqlite3" || echo "clean"
```

Then create an empty repository on GitHub and push to it.

**b. Create the project.** On railway.app: *New Project* → *Deploy from GitHub
repo* → pick the repository.

**c. Add Postgres.** *New* → *Database* → *PostgreSQL*. Railway exposes
`PGHOST`, `PGUSER` and friends; map them in your service variables:

```
DB_NAME=${{Postgres.PGDATABASE}}
DB_USER=${{Postgres.PGUSER}}
DB_PASSWORD=${{Postgres.PGPASSWORD}}
DB_HOST=${{Postgres.PGHOST}}
DB_PORT=${{Postgres.PGPORT}}
DB_SSLMODE=require
```

**d. Add the rest of the variables** from the table above.

**e. Add the worker.** *New* → *Empty Service* → same repository, then set its
start command to `python manage.py qcluster`. It needs the identical
environment variables, database included.

**f. Add your domain** under *Settings* → *Networking*, and make sure
`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` match it exactly.

**g. First deploy.** The `release` command in the `Procfile` runs migrations
and registers the job schedules automatically. Then create your administrator:

```bash
railway run python manage.py createsuperuser
```

---

## 3. A VPS — more control, more work

Suits you if you already run servers, or want the lowest cost at scale.
Assumes Ubuntu with PostgreSQL, nginx and certbot installed.

**a. System user and code**

```bash
sudo adduser --system --group academy && sudo -u academy git clone <your-repo> /srv/academy
```

**b. Dependencies**

```bash
cd /srv/academy && sudo -u academy python3 -m venv .venv && sudo -u academy .venv/bin/pip install -r requirements.txt
```

**c. Database**

```bash
sudo -u postgres createuser academy --pwprompt && sudo -u postgres createdb academy -O academy
```

**d. Environment.** Write `/srv/academy/.env` with the variables above, then
lock it down — it holds the key to every stored OAuth token:

```bash
sudo chmod 600 /srv/academy/.env && sudo chown academy:academy /srv/academy/.env
```

**e. Migrate, collect static, register schedules**

```bash
cd /srv/academy && sudo -u academy .venv/bin/python manage.py migrate && sudo -u academy .venv/bin/python manage.py collectstatic --noinput && sudo -u academy .venv/bin/python manage.py setup_schedules
```

**f. Two systemd units.** `/etc/systemd/system/academy-web.service`:

```ini
[Unit]
Description=100DaysAI Academy Tracker (web)
After=network.target postgresql.service

[Service]
User=academy
WorkingDirectory=/srv/academy
ExecStart=/srv/academy/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 120
Restart=always

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/academy-worker.service` is identical except for two lines:

```ini
Description=100DaysAI Academy Tracker (worker)
ExecStart=/srv/academy/.venv/bin/python manage.py qcluster
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now academy-web academy-worker
```

**g. nginx and TLS.** Proxy to `127.0.0.1:8000`, forwarding `Host` and
`X-Forwarded-Proto` — the app reads the latter to know it is behind HTTPS.
Then `sudo certbot --nginx -d tracker.yourdomain.com`.

---

## 4. After the first deploy

Work through these in order:

1. **Create your administrator** — `manage.py createsuperuser`.
2. **Enrol two-factor authentication.** Super Admins are required to have it;
   the Staff page flags anyone who has not. Enrol a second administrator too,
   so a lost phone does not lock you out of your own system.
3. **Check the worker is alive.** The Django admin has a *Scheduled tasks*
   section under Django Q. Each job should show a future run time. If they are
   all blank, the worker is not running.
4. **Confirm the schedules exist** — `manage.py setup_schedules` is idempotent,
   so run it again if unsure.
5. **Set your thresholds** on the Settings page. The research window defaults
   to 15 days; a batch can override it.
6. **Create your first batch** before enrolling anyone — every student must
   belong to one.
7. **Turn on database backups** at your provider, and verify a restore works
   before you depend on it.

### Verifying it actually works

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://tracker.yourdomain.com/login/
```

Should print `200`. Then trigger a scan by hand; the response counts the rules
evaluated and alerts created:

```bash
curl -sS -X POST -H "Authorization: Bearer YOUR_CRON_SECRET" https://tracker.yourdomain.com/api/cron/deadline-scan/
```

---

## 5. Hosts without a worker

Every job is also reachable over HTTP, protected by `CRON_SECRET`. Point an
external scheduler at these and skip the worker process entirely:

| Job | Suggested cadence |
|---|---|
| `deadline-scan` | every 6 hours |
| `dispatch-notifications` | every 5 minutes |
| `recalculate-deadlines` | daily |
| `flag-inactive` | daily |
| `youtube-sync` | every 12 hours |
| `prune-sessions` | weekly |

```bash
curl -X POST -H "Authorization: Bearer YOUR_CRON_SECRET" https://tracker.yourdomain.com/api/cron/deadline-scan/
```

The endpoint refuses every request when `CRON_SECRET` is unset, rather than
running unauthenticated.

---

## 6. Integrations, when you are ready

None of these block deployment. Each stays off until its keys are present, and
the Settings page shows the current state of every one.

- **YouTube public figures** — `YOUTUBE_API_KEY` from a Google Cloud project
  with *YouTube Data API v3* enabled. Gives subscribers, views, video counts
  and upload dates for any channel with a recorded channel ID.
- **YouTube private analytics** — additionally *YouTube Analytics API*, an
  OAuth client, and `GOOGLE_OAUTH_REDIRECT_URI` matching your domain exactly.
  Impressions, CTR, average view duration and retention are obtainable *only*
  with each student's own consent; there is no other lawful route. If more than
  100 students will connect, start Google's verification early — review takes
  weeks.
- **Email** — `RESEND_API_KEY`. Without it, alert emails are recorded as
  *skipped, with the reason* rather than silently lost.
- **SMS / WhatsApp** — Twilio and Meta Cloud API credentials.
- **LMS** — `LMS_WEBHOOK_SECRET` for the signed webhook receiver. CSV import
  needs no configuration at all.
