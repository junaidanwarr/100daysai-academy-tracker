"""
Test settings.

Production runs on PostgreSQL; the tests run on in-memory SQLite so the suite
executes on any machine without a database to provision. Nothing in the schema
is Postgres-specific — no array or range columns — so the two agree on
everything the tests assert.

    python manage.py test apps --settings=config.settings_test
"""

from config.settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

# Deterministic, and fast enough that the sync tests are not dominated by key
# derivation. Never used outside the test runner.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Fixed so the AES-GCM helpers exercise the real code path rather than raising
# "ENCRYPTION_KEY is not set" on a machine with no .env.
ENCRYPTION_KEY = "YWNhZGVteS10cmFja2VyLXRlc3Qta2V5LTMyYnl0ZSE="

SECRET_KEY = "test-only-secret-key-not-used-anywhere-else"
DEBUG = False

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
