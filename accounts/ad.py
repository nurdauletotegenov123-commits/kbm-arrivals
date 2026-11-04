"""Вход по доменной учётной записи (Active Directory) и синхронизация сотрудников.

Пароль проверяет контроллер домена — на сайте он не хранится. ФИО, почта, телефон и роль
берутся из AD: роль — по группе AD (AD_GROUP_*), без группы — AD_DEFAULT_ROLE.
Подрядчики и служебные локальные аккаунты (admin, ohrana…) входят как раньше, по паролю сайта.

Библиотека ldap3 — чистый Python, ставится и на Windows, и в Docker.
"""
import logging
import re
import ssl

from django.conf import settings
from django.contrib.auth.backends import BaseBackend
from ldap3 import NONE, SIMPLE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from .models import User

logger = logging.getLogger(__name__)
R = User.Role
ROLE_PRIORITY = [R.ADMIN, R.CHIEF, R.SECURITY, R.DISPATCHER, R.CAMP, R.SUPPLY]
ATTRS = ["sAMAccountName", "displayName", "mail", "title", "mobile", "telephoneNumber", "memberOf",
         "userAccountControl", "distinguishedName"]
UAC_DISABLED = 0x2
SAM_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ADError(Exception):
    """AD недоступен или настроен неверно (не путать с неверным паролем)."""


def enabled():
    return settings.AD_ENABLED


def server(host, mode):
    """mode: ldaps (636, шифрование сразу), starttls (389 → шифрование), plain (389, пароль открытым текстом)."""
    tls = Tls(validate=ssl.CERT_REQUIRED if settings.AD_CA_CERT else ssl.CERT_NONE,
              ca_certs_file=settings.AD_CA_CERT or None)
    if mode == "ldaps":
        return Server(host, port=636, use_ssl=True, tls=tls, get_info=NONE, connect_timeout=5)
    return Server(host, port=389, tls=tls, get_info=NONE, connect_timeout=5)


def connect(login, password, host=None, mode=None):
    """Открыть соединение под учётной записью. Неверный логин/пароль → None, AD недоступен → ADError."""
    if not password:  # пустой пароль AD принимает как анонимный вход — не пускаем
        return None
    mode = mode or settings.AD_SECURITY
    hosts = [host] if host else [h.strip() for h in settings.AD_HOST.split(",") if h.strip()]
    user = login if "@" in login else f"{login}@{settings.AD_DOMAIN}"
    last_error = None
    for h in hosts:  # несколько контроллеров — пробуем по очереди
        try:
            conn = Connection(server(h, mode), user=user, password=password, authentication=SIMPLE,
                              receive_timeout=10, auto_referrals=False, raise_exceptions=False)
            conn.open()
            if mode == "starttls" and not conn.start_tls():
                raise ADError(f"{h}: StartTLS не удался ({conn.result.get('description')})")
            if conn.bind():
                return conn
            if conn.result.get("description") == "invalidCredentials":
                return None
            raise ADError(f"{h}: {conn.result.get('description')} {conn.result.get('message', '')}".strip())
        except (LDAPException, OSError) as e:
            last_error = e
            logger.warning("AD %s (%s): %s", h, mode, e)
    raise ADError(str(last_error) if last_error else "не задан AD_HOST")


def sam_from_login(login):
    """«KBM\\ivanov», «ivanov@kbm.kz», «Ivanov» → «ivanov». None, если на доменный логин не похоже."""
    login = (login or "").strip()
    if "\\" in login:
        login = login.split("\\", 1)[1]
    if "@" in login:
        login = login.split("@", 1)[0]
    return login.lower() if SAM_RE.match(login) else None


def _first(entry, name):
    value = entry.get(name)
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def find(conn, sam):
    """Запись сотрудника по sAMAccountName или None."""
    flt = f"(&(objectCategory=person)(objectClass=user)(sAMAccountName={escape_filter_chars(sam)}))"
    conn.search(settings.AD_BASE_DN, flt, SUBTREE, attributes=ATTRS)
    found = [r for r in conn.response or [] if r.get("type") == "searchResEntry"]
    if not found:
        return None
    info = _info(found[0]["attributes"])
    _add_nested_role_groups(conn, info)
    return info


def group_dn(conn, name):
    root = ",".join(f"DC={part}" for part in settings.AD_DOMAIN.split("."))
    conn.search(root, f"(&(objectClass=group)(cn={escape_filter_chars(name)}))", SUBTREE, attributes=["cn"])
    found = [r for r in conn.response or [] if r.get("type") == "searchResEntry"]
    return found[0]["dn"] if found else None


def _add_nested_role_groups(conn, info):
    """Группы ролей, в которые сотрудник входит через другие группы (группа в группе)."""
    for name in filter(None, settings.AD_GROUPS.values()):
        if name.lower() in info["groups"]:
            continue
        dn = group_dn(conn, name)
        if not dn:
            continue
        conn.search(settings.AD_BASE_DN,
                    f"(&(sAMAccountName={escape_filter_chars(info['username'])})"
                    f"(memberOf:1.2.840.113556.1.4.1941:={escape_filter_chars(dn)}))", SUBTREE, attributes=["cn"])
        if any(r.get("type") == "searchResEntry" for r in conn.response or []):
            info["groups"].add(name.lower())


def _info(a):
    groups = a.get("memberOf") or []
    return {
        "username": _first(a, "sAMAccountName").lower(),
        "full_name": _first(a, "displayName"),
        "email": _first(a, "mail"),
        "phone": _first(a, "mobile") or _first(a, "telephoneNumber"),
        "title": _first(a, "title"),
        "dn": _first(a, "distinguishedName"),
        "groups": {g.split(",", 1)[0].split("=", 1)[-1].lower() for g in groups},
        "disabled": bool(int(_first(a, "userAccountControl") or 0) & UAC_DISABLED),
    }


def role_for(info, current=None):
    """Роль по группам AD. None — доступа нет.
    Если группы ролей не настроены, роль существующего пользователя не трогаем (её ставит администратор)."""
    groups = {role: (settings.AD_GROUPS.get(role) or "").lower() for role in ROLE_PRIORITY}
    for role in ROLE_PRIORITY:
        if groups[role] and groups[role] in info["groups"]:
            return role
    if current and not any(groups.values()):
        return current
    return settings.AD_DEFAULT_ROLE or None


def apply(info, user=None):
    """Создать или обновить пользователя по данным AD. Возвращает (user, {поле: [было, стало]})."""
    if user is None:
        user = User(username=info["username"], from_ad=True)
        user.set_unusable_password()
    role = role_for(info, user.role if user.pk else None)
    new = {
        "full_name": info["full_name"] or user.full_name,
        "email": info["email"],
        "phone": info["phone"],
        "role": role or user.role or R.APPLICANT,
        "is_active": bool(role) and not info["disabled"],
    }
    changes = {k: [getattr(user, k), v] for k, v in new.items() if user.pk is None or getattr(user, k) != v}
    for k, v in new.items():
        setattr(user, k, v)
    user.is_staff = user.is_superuser = user.role == R.ADMIN
    if changes or user.pk is None:
        user.save()
    return user, changes


class ADBackend(BaseBackend):
    """Проверка логина и пароля в AD. Локальные аккаунты (не из AD) этот вход не перехватывает."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not enabled() or not password:
            return None
        sam = sam_from_login(username)
        if not sam:
            return None
        existing = User.objects.filter(username__iexact=sam).first()
        if existing and not existing.from_ad:
            return None  # admin, ohrana, подрядчики — входят по паролю сайта
        try:
            conn = connect(sam, password)
        except ADError as e:
            logger.error("Вход через AD недоступен: %s", e)
            return None
        if conn is None:
            return None
        try:
            info = find(conn, sam)
        finally:
            conn.unbind()
        if not info:
            return None  # учётка есть, но вне AD_BASE_DN — не сотрудник КБМ
        user, changes = apply(info, existing)
        if changes:
            from audit.models import AuditLog
            from audit.utils import log_action

            action = AuditLog.Action.USER_UPDATE if existing else AuditLog.Action.USER_CREATE
            log_action(request, action, user, {"источник": "AD", **{k: v for k, v in changes.items()}}, user=user)
        return user if user.is_active else None

    def get_user(self, user_id):
        user = User.objects.filter(pk=user_id).first()
        return user if user and user.is_active else None
