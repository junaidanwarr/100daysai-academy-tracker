"""
Django settings for the 100DaysAI Academy Tracking System.

Configuration that changes behaviour at runtime (research window length, alert
thresholds, sync cadence) deliberately lives in the SystemSetting table rather
than here, so the academy can tune it without a deploy.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    return (env(key, str(default)) or "").strip().lower() in {"1", "true", "yes", "on"}


def env_int(key: str, default: int) -> int:
    try:
        return int(env(key, str(default)) or default)
    except ValueError:
        return default


# --- Core -------------------------------------------------------------------

SECRET_KEY = env("SECRET_KEY", "insecure-development-key-replace-before-deploying")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in (env("ALLOWED_HOSTS", "localhost,127.0.0.1") or "").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in (env("CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000") or "").split(",") if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_q",
    "django_htmx",
    "apps.core",
    "apps.accounts",
    "apps.academy",
    "apps.research",
    "apps.assignments",
    "apps.youtube",
    "apps.monitoring",
    "apps.completion",
    "apps.portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    # Blocks a Super Admin who has not cleared the TOTP challenge, and records
    # the request IP for the audit trail.
    "apps.accounts.middleware.MfaEnforcementMiddleware",
    "apps.core.middleware.AuditContextMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.navigation",
            ],
        },
    },
]

# --- Database ---------------------------------------------------------------

def _database_from_url(url: str) -> dict | None:
    """
    Parses a single connection string into Django's DATABASES shape.

    Render, Heroku, Neon, Supabase and Railway all hand out one DATABASE_URL
    rather than separate host/user/password variables. Parsed with the standard
    library so this costs no extra dependency.
    """
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        return None

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": (parsed.path or "/").lstrip("/") or "postgres",
        # Passwords routinely contain characters that must be percent-encoded
        # in a URL; without unquoting, authentication fails confusingly.
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
        "OPTIONS": {},
    }


_database_url = env("DATABASE_URL", "")
_parsed_database = _database_from_url(_database_url) if _database_url else None

DATABASES = {
    "default": _parsed_database
    or {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "postgres"),
        "USER": env("DB_USER", "postgres"),
        "PASSWORD": env("DB_PASSWORD", "postgres"),
        "HOST": env("DB_HOST", "127.0.0.1"),
        "PORT": env("DB_PORT", "5432"),
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 60),
        "OPTIONS": {},
    }
}

# Managed Postgres is reached over the public internet, so require TLS unless
# the operator has said otherwise. A local database stays plaintext.
if _parsed_database and not env("DB_SSLMODE") and "localhost" not in DATABASES["default"]["HOST"]:
    DATABASES["default"]["OPTIONS"]["sslmode"] = "require"

# Connection poolers (and the disposable local PGlite server) do not support
# server-side prepared statements or held cursors.
if env_bool("DB_POOLED", False):
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
    DATABASES["default"]["CONN_MAX_AGE"] = 0
    DATABASES["default"]["OPTIONS"]["prepare_threshold"] = None

if env("DB_SSLMODE"):
    DATABASES["default"]["OPTIONS"]["sslmode"] = env("DB_SSLMODE")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

# --- Authentication ---------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

SESSION_COOKIE_AGE = env_int("SESSION_TTL_MINUTES", 480) * 60
SESSION_SAVE_EVERY_REQUEST = True  # sliding expiry
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# Lockout policy, enforced in apps.accounts.services.
MAX_FAILED_LOGIN_ATTEMPTS = env_int("MAX_FAILED_LOGIN_ATTEMPTS", 8)
LOGIN_LOCKOUT_MINUTES = env_int("LOGIN_LOCKOUT_MINUTES", 15)

# --- Security ---------------------------------------------------------------

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# AES-256-GCM key for OAuth refresh tokens, LMS credentials and TOTP secrets.
# Must decode to exactly 32 bytes. Rotating it invalidates every stored token.
ENCRYPTION_KEY = env("ENCRYPTION_KEY", "")

# --- Background jobs --------------------------------------------------------

# django-q2 with the ORM broker: the queue lives in the same Postgres database,
# so there is no Redis to install or operate.
Q_CLUSTER = {
    "name": "academy",
    "workers": env_int("Q_WORKERS", 2),
    "timeout": 600,
    "retry": 900,
    "max_attempts": 3,
    "queue_limit": 50,
    "bulk": 10,
    "orm": "default",
    "save_limit": 250,
    "catch_up": False,
}

CRON_SECRET = env("CRON_SECRET", "")

# --- Integrations -----------------------------------------------------------

GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", "")
GOOGLE_OAUTH_REDIRECT_URI = env("GOOGLE_OAUTH_REDIRECT_URI", "")
YOUTUBE_API_KEY = env("YOUTUBE_API_KEY", "")
YOUTUBE_DAILY_QUOTA_UNITS = env_int("YOUTUBE_DAILY_QUOTA_UNITS", 10000)
# Off by default: the monetary scope puts an alarming line on every student's
# consent screen for data most channels do not have. Turn it on only if the
# academy genuinely tracks revenue.
YOUTUBE_TRACK_REVENUE = env_bool("YOUTUBE_TRACK_REVENUE", False)

RESEND_API_KEY = env("RESEND_API_KEY", "")
NOTIFY_FROM_EMAIL = env("NOTIFY_FROM_EMAIL", "academy@100daysai.example")
TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = env("TWILIO_FROM_NUMBER", "")
WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_ACCESS_TOKEN = env("WHATSAPP_ACCESS_TOKEN", "")

LMS_ADAPTER = env("LMS_ADAPTER", "MANUAL")
LMS_WEBHOOK_SECRET = env("LMS_WEBHOOK_SECRET", "")

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend"

# --- Static and media -------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
FILE_MAX_UPLOAD_MB = env_int("FILE_MAX_UPLOAD_MB", 25)

# --- Localisation -----------------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "propagate": False, "handlers": ["console"]},
    },
}
