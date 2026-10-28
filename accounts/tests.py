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
