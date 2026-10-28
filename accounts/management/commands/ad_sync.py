"""Синхронизация сотрудников с Active Directory (запускать по расписанию, например раз в час).

    python manage.py ad_sync            # применить
    python manage.py ad_sync --dry-run  # только показать, что изменится

Нужна служебная учётка только для чтения: AD_BIND_USER / AD_BIND_PASSWORD.
- участники групп ролей (AD_GROUP_*) заводятся на сайте заранее, до первого входа;
- у уже заведённых из AD обновляются ФИО, почта, телефон и роль;
- отключённые или удалённые в AD — отключаются на сайте. Локальные аккаунты не трогаются.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from ldap3 import SUBTREE
from ldap3.utils.conv import escape_filter_chars

from accounts import ad
from accounts.models import User
from audit.models import AuditLog
from audit.utils import log_action


class Command(BaseCommand):
    help = "Синхронизировать сотрудников с Active Directory"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **opts):
        if not ad.enabled():
            raise CommandError("AD не настроен: AD_HOST, AD_DOMAIN, AD_BASE_DN.")
        if not (settings.AD_BIND_USER and settings.AD_BIND_PASSWORD):
            raise CommandError("Нужна служебная учётка: AD_BIND_USER и AD_BIND_PASSWORD.")
        try:
            conn = ad.connect(settings.AD_BIND_USER, settings.AD_BIND_PASSWORD)
        except ad.ADError as e:
            raise CommandError(f"AD недоступен: {e}")
        if conn is None:
            raise CommandError("Неверный логин или пароль служебной учётки.")
        try:
            people = self._group_members(conn)
            for user in User.objects.filter(from_ad=True):
                if user.username not in people:
                    people[user.username] = ad.find(conn, user.username)
        finally:
            conn.unbind()

        stats = {"новых": 0, "изменено": 0, "отключено": 0}
        with transaction.atomic():
            for username, info in sorted(people.items()):
                existing = User.objects.filter(username__iexact=username).first()
                if existing and not existing.from_ad:
                    continue  # локальный аккаунт с тем же логином — не трогаем
                if info is None:  # удалён из AD или ушёл из AD_BASE_DN
                    if existing and existing.is_active:
                        self.stdout.write(f"  отключить {username}: нет в AD")
                        stats["отключено"] += 1
                        if not dry_run:
                            existing.is_active = False
                            existing.save(update_fields=["is_active"])
                            log_action(None, AuditLog.Action.USER_UPDATE, existing,
                                       {"источник": "AD", "is_active": [True, False]}, object_repr=str(existing))
                    continue
                if dry_run:
                    role = ad.role_for(info, existing.role if existing else None)
                    self.stdout.write(f"  {'обновить' if existing else 'создать'} {username}: {info['full_name']}, роль {role}")
                    continue
                user, changes = ad.apply(info, existing)
                if changes:
                    key = "изменено" if existing else "новых"
                    if existing and changes.get("is_active") == [True, False]:
                        key = "отключено"
                    stats[key] += 1
                    log_action(None, AuditLog.Action.USER_UPDATE if existing else AuditLog.Action.USER_CREATE, user,
                               {"источник": "AD", **changes}, object_repr=str(user))
            if dry_run:
                transaction.set_rollback(True)
        self.stdout.write(self.style.SUCCESS(
            ("Проверка (ничего не сохранено): " if dry_run else "Готово: ") + ", ".join(f"{k} {v}" for k, v in stats.items())))

    def _group_members(self, conn):
        """{логин: данные} всех участников групп ролей."""
        people = {}
        for role, name in settings.AD_GROUPS.items():
            if not name:
                continue
            found = ad.group_dn(conn, name)
            if not found:
                self.stdout.write(self.style.WARNING(f"  группа «{name}» ({role}) не найдена в AD"))
                continue
            dn = escape_filter_chars(found)
            # 1.2.840.113556.1.4.1941 — с учётом вложенных групп
            entries = conn.extend.standard.paged_search(
                settings.AD_BASE_DN, f"(&(objectCategory=person)(objectClass=user)(memberOf:1.2.840.113556.1.4.1941:={dn}))",
                SUBTREE, attributes=ad.ATTRS, paged_size=500, generator=False)
            for r in entries:
                if r.get("type") == "searchResEntry":
                    info = ad._info(r["attributes"])
                    people[info["username"]] = info
        return people
