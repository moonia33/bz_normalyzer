"""Django nustatymai. Konfidencialios reikšmės imamos iš aplinkos kintamųjų."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = _env("DJANGO_SECRET_KEY", "django-insecure-dev-only-change-me")
DEBUG = _env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [
    host.strip()
    for host in (_env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1") or "").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.corecfg",
    "apps.catalog",
    "apps.suppliers",
    "apps.ingestion",
    "apps.mapping",
    "apps.pricing",
    "apps.catalog_export",
    "apps.api",
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
WSGI_APPLICATION = "config.wsgi.application"

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
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _env("POSTGRES_DB", "bz_normalyzer"),
        "USER": _env("POSTGRES_USER", "postgres"),
        "PASSWORD": _env("POSTGRES_PASSWORD", ""),
        "HOST": _env("POSTGRES_HOST", "localhost"),
        "PORT": _env("POSTGRES_PORT", "5432"),
    }
}

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in (
        _env(
            "CSRF_TRUSTED_ORIGINS",
            "http://localhost:8000,https://localhost",
        )
        or ""
    ).split(",")
    if origin.strip()
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "lt"
TIME_ZONE = "Europe/Vilnius"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Viešam katalogo API. Tuščia reikšmė = autentifikacija išjungta (tik DEBUG).
API_KEY = _env("DJANGO_API_KEY", "")

# Shop (katalogo gavėjas) push integracija.
# Auth: JWT per cookie – POST /api/v1/auth/login su staff vartotoju.
SHOP_API_BASE_URL = _env("SHOP_API_BASE_URL", "")
SHOP_API_KEY = _env("SHOP_API_KEY", "")
SHOP_USER_EMAIL = _env("SHOP_USER_EMAIL", "")
SHOP_USER_PASSWORD = _env("SHOP_USER_PASSWORD", "")

# Numatytasis PVM tarifas, naudojamas kai tiekėjas neturi savo.
DEFAULT_VAT_RATE = _env("DEFAULT_VAT_RATE", "0.21")
