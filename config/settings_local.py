"""
Local exploration settings — SQLite in a file, so the whole application can be
run and clicked through without provisioning PostgreSQL.

    python manage.py migrate --settings=config.settings_local
    python manage.py seed_demo --settings=config.settings_local
    python manage.py runserver --settings=config.settings_local

Production is PostgreSQL and nothing here changes that. This exists so a
reviewer can see the system working on a laptop with nothing installed; the
database lands in local.sqlite3, which you can delete to start over.

Do not deploy with these settings: DEBUG is on and the keys below are fixed.
"""

from config.settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "local.sqlite3"}}  # noqa: F405

DEBUG = True
# "testserver" is what Django's own test client sends, so ad-hoc smoke checks
# through `manage.py shell` work against these settings too.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]

# Fixed so the encrypted fields work out of the box. Local demonstration data
# only — a real deployment generates its own and keeps it out of the repository.
ENCRYPTION_KEY = "YWNhZGVteS10cmFja2VyLWxvY2FsLWtleS0zMmJ5dCE="
SECRET_KEY = "local-only-secret-key-do-not-deploy-with-this"

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
