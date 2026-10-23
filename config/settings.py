"""Настройки проекта «Учёт прибытия на месторождение».

Все параметры, зависящие от окружения, читаются из переменных окружения
(см. .env.example). Без переменных проект запускается в режиме разработки
на SQLite.
"""
import os
from pathlib import Path

from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-key-change-me" if DEBUG else None)
if not SECRET_KEY:
    raise RuntimeError("Не задан DJANGO_SECRET_KEY")

# Домен сайта (когда будет куплен). Нужен для HTTPS — см. README, раздел «HTTPS».
DOMAIN = env("DOMAIN", "").strip()
# Сайт работает по HTTPS за прокси Caddy (docker compose --profile https).
HTTPS = env_bool("DJANGO_HTTPS", False)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1") + ([DOMAIN] if DOMAIN else [])
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "") + ([f"https://{DOMAIN}"] if DOMAIN and HTTPS else [])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "audit",
    "visits",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

try:
    import whitenoise  # noqa: F401
except ImportError:  # в режиме разработки whitenoise не обязателен
    MIDDLEWARE.remove("whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

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
                "accounts.context_processors.nav",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if env("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": env("POSTGRES_HOST"),
            "PORT": env("POSTGRES_PORT", "5432"),
            "NAME": env("POSTGRES_DB", "kbm"),
            "USER": env("POSTGRES_USER", "kbm"),
            "PASSWORD": env("POSTGRES_PASSWORD", ""),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

# --- Вход через корпоративный AD/LDAP (подключается позже) ---
# Чтобы включить: установить пакет django-auth-ldap, задать LDAP_SERVER_URI
# и остальные LDAP_* переменные (см. README, раздел «AD/LDAP»).
if env("LDAP_SERVER_URI"):
    import ldap
    from django_auth_ldap.config import LDAPSearch

    AUTH_LDAP_SERVER_URI = env("LDAP_SERVER_URI")
    AUTH_LDAP_BIND_DN = env("LDAP_BIND_DN", "")
    AUTH_LDAP_BIND_PASSWORD = env("LDAP_BIND_PASSWORD", "")
    AUTH_LDAP_USER_SEARCH = LDAPSearch(
        env("LDAP_USER_BASE", ""), ldap.SCOPE_SUBTREE, env("LDAP_USER_FILTER", "(sAMAccountName=%(user)s)")
    )
    AUTH_LDAP_USER_ATTR_MAP = {"full_name": "displayName", "email": "mail"}
    AUTH_LDAP_ALWAYS_UPDATE_USER = True
    AUTHENTICATION_BACKENDS.insert(0, "django_auth_ldap.backend.LDAPBackend")

# --- Язык и время ---
LANGUAGE_CODE = "ru"
LANGUAGES = [
    ("ru", _("Русский")),
    ("kk", _("Қазақша")),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = env("TIME_ZONE", "Asia/Aqtau")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
if not DEBUG and "whitenoise.middleware.WhiteNoiseMiddleware" in MIDDLEWARE:
    # В рабочем режиме файлы получают хэш в имени (app.3f9c1a.css) — кэш браузера не мешает обновлениям.
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Безопасность ---
SESSION_COOKIE_AGE = 60 * 60 * 12  # 12 часов — одна смена
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = HTTPS
CSRF_COOKIE_SECURE = HTTPS
# За прокси: доверяем его заголовкам о протоколе и IP клиента (для журнала).
# Без прокси эти заголовки может подделать кто угодно, поэтому они игнорируются.
BEHIND_PROXY = HTTPS or env_bool("DJANGO_BEHIND_PROXY", False)
if BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
if HTTPS:
    # HSTS и перенаправление http → https делает Caddy (docker/Caddyfile).
    SILENCED_SYSTEM_CHECKS = ["security.W004", "security.W008"]
X_FRAME_OPTIONS = "DENY"

# --- Почта ---
# Без EMAIL_HOST письма печатаются в консоль (удобно для проверки).
if env("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST")
    EMAIL_PORT = int(env("EMAIL_PORT", "25"))
    EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
    EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
    EMAIL_TIMEOUT = 10
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "arrivals@kbm.kz")
NOTIFICATIONS_ENABLED = env_bool("NOTIFICATIONS_ENABLED", True)
SITE_URL = env("SITE_URL", "http://localhost:8000")

# Сколько дней после даты прибытия «не прибывший» остаётся в отдельном блоке.
NO_SHOW_DAYS = int(env("NO_SHOW_DAYS", "2"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    # 403/404 — штатная ситуация (чужая роль), не засоряем лог трассировками
    "loggers": {"django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False}},
}
