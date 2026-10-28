import os
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from audit.models import AuditLog

from . import telegram
from .forms import normalize_phone
from .management.commands.telegram_bot import Command
from .models import User


class SignupTests(TestCase):
    def data(self, **kw):
        d = {"full_name": "Подрядчиков Пётр", "organization": "ТОО Подряд", "phone": "8 (701) 123-45-67",
             "email": "", "password1": "Kbm-pass-2026", "password2": "Kbm-pass-2026", "website": ""}
        d.update(kw)
        return d

    def test_phone_normalization(self):
        for raw in ("+7 701 123 45 67", "87011234567", "7011234567", "+7(701)123-45-67"):
            self.assertEqual(normalize_phone(raw), "77011234567")
        self.assertIsNone(normalize_phone("12345"))

    def test_signup_creates_applicant_and_logs_in(self):
        resp = self.client.post(reverse("signup"), self.data())
        self.assertRedirects(resp, reverse("request_list"))
        user = User.objects.get(username="77011234567")
        self.assertEqual(user.role, User.Role.APPLICANT)
        self.assertTrue(user.self_registered)
        self.assertEqual(user.organization, "ТОО Подряд")
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.USER_SIGNUP).exists())

    def test_login_with_phone_in_any_format(self):
        self.client.post(reverse("signup"), self.data())
        self.client.logout()
        resp = self.client.post(reverse("login"), {"username": "+7 701 123-45-67", "password": "Kbm-pass-2026"})
        self.assertRedirects(resp, reverse("home"), target_status_code=302)

    def test_duplicate_phone_and_honeypot(self):
        self.client.post(reverse("signup"), self.data())
        self.client.logout()
        self.assertContains(self.client.post(reverse("signup"), self.data(phone="+77011234567")), "уже зарегистрирован")
        self.client.post(reverse("signup"), self.data(phone="+77019999999", website="spam"))
        self.assertFalse(User.objects.filter(username="77019999999").exists())

    @override_settings(SIGNUPS_PER_HOUR=2)
    def test_signup_throttled_per_ip(self):
        for i in range(3):
            self.client.post(reverse("signup"), self.data(phone=f"+7701000000{i}"))
            self.client.logout()
        self.assertEqual(User.objects.count(), 2)

    @override_settings(LOGIN_FAILS_LIMIT=3)
    def test_login_throttled_after_failures(self):
        User.objects.create_user("guard", password="right-pass-1")
        for _ in range(3):
            self.client.post(reverse("login"), {"username": "guard", "password": "wrong"})
        resp = self.client.post(reverse("login"), {"username": "guard", "password": "right-pass-1"}, follow=True)
        self.assertContains(resp, "Слишком много неудачных попыток")

    @override_settings(SIGNUP_ENABLED=False)
    def test_signup_can_be_disabled(self):
        self.assertEqual(self.client.get(reverse("signup")).status_code, 404)


@override_settings(TELEGRAM_BOT_TOKEN="123:abc", TELEGRAM_BOT_USERNAME="kbm_test_bot", TELEGRAM_ENABLED=True)
class TelegramLinkTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("zayav", password="x-pass-123", full_name="Заявитель Тестовый")
        self.sent = []
        patcher = mock.patch("accounts.telegram.api", side_effect=lambda method, **p: self.sent.append((method, p)) or {})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bot = Command()

    def start(self, text, chat_id=555):
        self.bot.on_message({"chat": {"id": chat_id, "type": "private"}, "text": text})

    def test_connect_by_link_and_stop(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse("telegram_connect"))
        self.user.refresh_from_db()
        self.assertEqual(resp["Location"], f"https://t.me/kbm_test_bot?start={self.user.telegram_token}")
        self.start(f"/start {self.user.telegram_token}")
        self.user.refresh_from_db()
        self.assertEqual(self.user.telegram_chat_id, "555")
        self.assertEqual(self.user.telegram_token, "")  # ссылка одноразовая
        self.assertIn("Telegram подключён", self.sent[-1][1]["text"])
        self.start("/stop")
        self.user.refresh_from_db()
        self.assertEqual(self.user.telegram_chat_id, "")

    def test_expired_or_wrong_token(self):
        telegram.link_url(self.user)
        User.objects.filter(pk=self.user.pk).update(telegram_token_at=timezone.now() - timedelta(days=2))
        self.start(f"/start {self.user.telegram_token}")
        self.start("/start чужой")
        self.user.refresh_from_db()
        self.assertEqual(self.user.telegram_chat_id, "")

    def test_profile_shows_button(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("own_password")), "Подключить Telegram")


AD_SETTINGS = dict(
    AD_ENABLED=True, AD_HOST="dc.test", AD_DOMAIN="KBM.KZ", AD_BASE_DN="OU=KBM JSC,DC=KBM,DC=KZ",
    AD_DEFAULT_ROLE="applicant", AD_GROUPS={"admin": "", "chief": "KBM-Propusk-Chief", "security": "",
                                             "dispatcher": "", "camp": ""},
    AUTHENTICATION_BACKENDS=["accounts.ad.ADBackend", "django.contrib.auth.backends.ModelBackend"],
)


def ad_info(**kw):
    info = {"username": "n_otegenov", "full_name": "Өтегенов Нұрдәулет Русланұлы", "email": "N_Otegenov@KBM.KZ",
            "phone": "+7 708 000 00 00", "title": "Техник", "dn": "CN=x", "groups": {"it_group"}, "disabled": False}
    info.update(kw)
    return info


@override_settings(**AD_SETTINGS)
class ADLoginTests(TestCase):
    def setUp(self):
        self.conn = mock.MagicMock()
        p1 = mock.patch("accounts.ad.connect", return_value=self.conn)
        p2 = mock.patch("accounts.ad.find", return_value=ad_info())
        self.connect, self.find = p1.start(), p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def login(self, username="KBM\\N_Otegenov", password="domain-pass"):
        return self.client.post(reverse("login"), {"username": username, "password": password})

    def test_login_formats_and_user_created_from_ad(self):
        from . import ad
        for raw in ("KBM\\N_Otegenov", "n_otegenov@kbm.kz", "N_Otegenov"):
            self.assertEqual(ad.sam_from_login(raw), "n_otegenov")
        self.assertIsNone(ad.sam_from_login("bad login!"))
        resp = self.login()
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username="n_otegenov")
        self.assertTrue(user.from_ad)
        self.assertFalse(user.has_usable_password())  # пароль хранит только AD
        self.assertEqual((user.role, user.email, user.full_name),
                         (User.Role.APPLICANT, "N_Otegenov@KBM.KZ", "Өтегенов Нұрдәулет Русланұлы"))
        self.connect.assert_called_with("n_otegenov", "domain-pass")

    def test_role_from_group_and_updates_on_next_login(self):
        self.find.return_value = ad_info(groups={"kbm-propusk-chief"})
        self.login()
        self.assertEqual(User.objects.get(username="n_otegenov").role, User.Role.CHIEF)
        self.client.logout()
        self.find.return_value = ad_info(groups=set())  # убрали из группы
        self.login()
        self.assertEqual(User.objects.get(username="n_otegenov").role, User.Role.APPLICANT)

    def test_wrong_password_disabled_and_empty_password(self):
        self.connect.return_value = None
        self.assertEqual(self.login().status_code, 200)
        self.connect.return_value = self.conn
        self.find.return_value = ad_info(disabled=True)
        self.assertEqual(self.login().status_code, 200)
        self.connect.reset_mock()
        self.login(password="")
        self.connect.assert_not_called()  # пустой пароль в AD = анонимный вход, не пускаем

    def test_local_accounts_not_taken_over_and_work_when_ad_down(self):
        from . import ad
        User.objects.create_user("ohrana", password="local-pass-1", role=User.Role.SECURITY)
        self.connect.side_effect = ad.ADError("DC недоступен")
        resp = self.login("ohrana", "local-pass-1")
        self.assertEqual(resp.status_code, 302)
        self.connect.assert_not_called()  # локальный логин в AD даже не проверяется
        self.client.logout()
        self.assertEqual(self.login().status_code, 200)  # AD недоступен — доменный вход не проходит, но сайт жив

    def test_no_default_role_means_no_access_without_group(self):
        with self.settings(AD_DEFAULT_ROLE=""):
            self.assertEqual(self.login().status_code, 200)
            self.assertFalse(User.objects.get(username="n_otegenov").is_active)


@override_settings(**AD_SETTINGS, AD_BIND_USER="svc_propusk", AD_BIND_PASSWORD="x")
class ADSyncTests(TestCase):
    def test_sync_creates_group_members_and_disables_gone(self):
        from django.core.management import call_command

        from .management.commands.ad_sync import Command as Sync

        gone = User.objects.create(username="uvolen", from_ad=True, full_name="Уволенный")
        local = User.objects.create(username="admin2", full_name="Локальный")
        with mock.patch("accounts.ad.connect", return_value=mock.MagicMock()), \
                mock.patch.object(Sync, "_group_members", return_value={"chief1": ad_info(username="chief1",
                                                                                               groups={"kbm-propusk-chief"})}), \
                mock.patch("accounts.ad.find", return_value=None):
            call_command("ad_sync", stdout=open(os.devnull, "w"))
        self.assertEqual(User.objects.get(username="chief1").role, User.Role.CHIEF)
        gone.refresh_from_db()
        local.refresh_from_db()
        self.assertFalse(gone.is_active)
        self.assertTrue(local.is_active)
