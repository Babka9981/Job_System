import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


def positive_env_int(name, default):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "development-only-secret")
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [x for x in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if x]
CSRF_TRUSTED_ORIGINS = [x for x in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if x]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "jobs.core",
    "jobs.models",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "jobs" / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "jobs" / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "vacancies"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "false").lower() == "true"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
PRIVATE_ROOT = BASE_DIR / os.environ.get("JOB_PRIVATE_ROOT", "private")
TEMPORARY_ROOT = BASE_DIR / os.environ.get("JOB_TEMPORARY_ROOT", "temporary")
_match_cache_setting = os.environ.get("JOB_MATCH_CACHE_ROOT", "").strip()
MATCH_CACHE_ROOT = Path(_match_cache_setting) if _match_cache_setting else PRIVATE_ROOT / "matching-cache"
if not MATCH_CACHE_ROOT.is_absolute():
    MATCH_CACHE_ROOT = PRIVATE_ROOT / MATCH_CACHE_ROOT
MATCH_CACHE_ROOT = MATCH_CACHE_ROOT.resolve()
if not MATCH_CACHE_ROOT.is_relative_to(PRIVATE_ROOT.resolve()):
    raise ImproperlyConfigured("JOB_MATCH_CACHE_ROOT must be inside JOB_PRIVATE_ROOT")
if MATCH_CACHE_ROOT.is_relative_to(TEMPORARY_ROOT.resolve()):
    raise ImproperlyConfigured("JOB_MATCH_CACHE_ROOT must not be inside JOB_TEMPORARY_ROOT")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": str(MATCH_CACHE_ROOT),
        "TIMEOUT": positive_env_int("JOB_MATCH_CACHE_TIMEOUT_SECONDS", 86400),
        "OPTIONS": {"MAX_ENTRIES": 10000},
    }
}
OWNER_USERNAME = os.environ.get("JOB_OWNER_USERNAME", "owner")
USER_TIME_ZONE = os.environ.get("JOB_TIME_ZONE", "Europe/Moscow")
