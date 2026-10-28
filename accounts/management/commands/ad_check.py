"""Проверка подключения к Active Directory — ничего не сохраняет.

    python manage.py ad_check                 # настройки из .env, спросит логин и пароль
    python manage.py ad_check --host AOS22-DC02.KBM.KZ --domain KBM.KZ --base "OU=KBM JSC,DC=KBM,DC=KZ"
"""
import getpass
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from ldap3 import Connection

from accounts import ad


class Command(BaseCommand):
    help = "Проверить вход через AD: какие режимы шифрования доступны, что AD знает о сотруднике и какая будет роль"

    def add_arguments(self, parser):
        parser.add_argument("--host")
        parser.add_argument("--domain")
        parser.add_argument("--base")
        parser.add_argument("--security", choices=["ldaps", "starttls", "plain"])
        parser.add_argument("--login", help="доменный логин (по умолчанию — текущий пользователь Windows)")

    def handle(self, *args, host=None, domain=None, base=None, security=None, login=None, **opts):
        settings.AD_HOST = host or settings.AD_HOST
        settings.AD_DOMAIN = domain or settings.AD_DOMAIN
        settings.AD_BASE_DN = base or settings.AD_BASE_DN
        settings.AD_SECURITY = security or settings.AD_SECURITY
        if not (settings.AD_HOST and settings.AD_DOMAIN and settings.AD_BASE_DN):
            raise CommandError("Задайте AD_HOST, AD_DOMAIN и AD_BASE_DN в .env (или --host --domain --base).")
        out = self.stdout.write
        out(f"Контроллеры: {settings.AD_HOST}\nДомен: {settings.AD_DOMAIN}\nГде искать: {settings.AD_BASE_DN}\n")

        out("1) Шифрованное подключение (без пароля):")
        working = []
        for h in [x.strip() for x in settings.AD_HOST.split(",") if x.strip()]:
            for mode in ("ldaps", "starttls"):
                try:
                    conn = Connection(ad.server(h, mode), receive_timeout=10)
                    conn.open()
                    ok = conn.start_tls() if mode == "starttls" else True
                    conn.unbind()
                except Exception as e:  # noqa: BLE001 — показываем любую причину
                    ok, why = False, str(e)[:120]
                else:
                    why = "" if ok else "сервер отказал в StartTLS"
                out(f"   {h} {mode:9} {'OK' if ok else 'нет'} {why}")
                if ok:
                    working.append(mode)
        if settings.AD_SECURITY != "plain" and settings.AD_SECURITY not in working:
            hint = f" Доступно: {', '.join(sorted(set(working)))}." if working else \
                " Шифрование недоступно — спросите ИТ про сертификат на контроллере домена (LDAPS)."
            self.stdout.write(self.style.WARNING(f"   Режим AD_SECURITY={settings.AD_SECURITY} не работает.{hint}"))

        login = login or input(f"\n2) Доменный логин [{os.environ.get('USERNAME', '')}]: ").strip() or os.environ.get("USERNAME", "")
        password = getpass.getpass("   Пароль (не отображается): ")
        try:
            conn = ad.connect(ad.sam_from_login(login) or login, password)
        except ad.ADError as e:
            raise CommandError(f"AD недоступен: {e}")
        if conn is None:
            raise CommandError("Неверный логин или пароль.")
        try:
            info = ad.find(conn, ad.sam_from_login(login))
        finally:
            conn.unbind()
        if not info:
            raise CommandError(f"Вход удался, но сотрудник не найден в {settings.AD_BASE_DN}.")
        out(self.style.SUCCESS("   Вход через AD работает."))
        out(f"\n3) Что сайт возьмёт из AD:\n   логин: {info['username']}\n   ФИО: {info['full_name']}\n"
            f"   почта: {info['email'] or '—'}\n   телефон: {info['phone'] or '—'}\n   должность: {info['title'] or '—'}\n"
            f"   отключён в AD: {'да' if info['disabled'] else 'нет'}\n   групп: {len(info['groups'])}")
        role = ad.role_for(info)
        out(f"   роль на сайте: {dict(ad.User.Role.choices).get(role, 'нет доступа') if role else 'нет доступа'}")
        out("\nНичего не сохранено.")
