"""Настройки проекта «Учёт прибытия на месторождение».

Все параметры, зависящие от окружения, читаются из переменных окружения
(см. .env.example). Без переменных проект запускается в режиме разработки
на SQLite.
"""
import os
import sys
from pathlib import Path

from django.utils.translation import gettext_lazy as _

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    """Локальный запуск без Docker: подхватить .env из папки проекта.
    Уже заданные переменные окружения (в Docker — из env_file) не перезаписываются."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():  # utf-8-sig: Блокнот Windows добавляет BOM
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if "test" not in sys.argv[1:2]:  # автотесты не зависят от локального .env
    _load_dotenv(BASE_DIR / ".env")


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

# --- Вход по доменной учётной записи (Active Directory) ---
# См. README, раздел «Вход через AD». Без AD_HOST / AD_DOMAIN / AD_BASE_DN — только локальные пароли.
AD_HOST = env("AD_HOST", "")  # контроллеры домена через запятую: AOS22-DC02.KBM.KZ
AD_DOMAIN = env("AD_DOMAIN", "")  # KBM.KZ — вход как login@KBM.KZ
AD_BASE_DN = env("AD_BASE_DN", "")  # где искать сотрудников: OU=KBM JSC,DC=KBM,DC=KZ
AD_SECURITY = env("AD_SECURITY", "ldaps")  # ldaps (636) | starttls (389) | plain (389, пароль открытым текстом)
AD_CA_CERT = env("AD_CA_CERT", "")  # корневой сертификат домена (.pem/.cer) — тогда сертификат DC проверяется
AD_BIND_USER = env("AD_BIND_USER", "")  # служебная учётка только для чтения — для синхронизации ad_sync
AD_BIND_PASSWORD = env("AD_BIND_PASSWORD", "")
AD_DEFAULT_ROLE = env("AD_DEFAULT_ROLE", "applicant")  # роль сотрудника без группы; пусто — без группы не пускать
AD_GROUPS = {  # группа AD (CN) → роль на сайте
    "admin": env("AD_GROUP_ADMIN", ""),
    "chief": env("AD_GROUP_CHIEF", ""),
    "security": env("AD_GROUP_SECURITY", ""),
    "dispatcher": env("AD_GROUP_DISPATCHER", ""),
    "camp": env("AD_GROUP_CAMP", ""),
}
AD_ENABLED = bool(AD_HOST and AD_DOMAIN and AD_BASE_DN)
if AD_ENABLED:
    AUTHENTICATION_BACKENDS.insert(0, "accounts.ad.ADBackend")

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
# Загруженные файлы (документы на груз). Наружу напрямую не отдаются — только через проверку прав.
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))
CARGO_FILE_MAX_MB = int(env("CARGO_FILE_MAX_MB", "10"))
CARGO_FILES_MAX = 10
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

# Telegram-бот для уведомлений (см. README, «Уведомления в Telegram»). Без токена кнопок Telegram нет.
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", "").lstrip("@")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_USERNAME)
SITE_URL = env("SITE_URL", "http://localhost:8000")

# Сколько дней после даты прибытия «не прибывший» остаётся в отдельном блоке.
NO_SHOW_DAYS = int(env("NO_SHOW_DAYS", "2"))
# Сколько дней подсвечивать перенос дат заявителем у КПП, кэмпа и диспетчера
DATE_CHANGE_DAYS = int(env("DATE_CHANGE_DAYS", "3"))

# Регистрация заявителей с телефона (подрядчики, гости). 0 — регистрацию закрыть, аккаунты заводит админ.
SIGNUP_ENABLED = env("SIGNUP_ENABLED", "1") == "1"
SIGNUPS_PER_HOUR = int(env("SIGNUPS_PER_HOUR", "10"))  # с одного IP
LOGIN_FAILS_LIMIT = int(env("LOGIN_FAILS_LIMIT", "10"))  # неудачных входов с одного IP за 15 минут

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    # 403/404 — штатная ситуация (чужая роль), не засоряем лог трассировками
    "loggers": {"django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False}},
}
