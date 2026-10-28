"""Бот уведомлений: принимает «Start» по ссылке с сайта и запоминает chat_id пользователя.

    python manage.py telegram_bot

Работает через long polling (getUpdates), поэтому серверу нужен только исходящий доступ
к api.telegram.org — внешний IP и домен не нужны. В docker-compose это сервис `bot`.
"""
import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from accounts import telegram
from accounts.models import User
from audit.models import AuditLog
from audit.utils import log_action

logger = logging.getLogger(__name__)

WHAT_YOU_GET = {
    User.Role.APPLICANT: "решение по вашим заявкам и машина, которую назначил диспетчер",
    User.Role.CHIEF: "новые заявки на согласование",
    User.Role.DISPATCHER: "согласованные заявки, кому нужна машина",
    User.Role.SECURITY: "согласованные заявки: кто едет и когда",
    User.Role.CAMP: "согласованные заявки с проживанием и питанием",
}
HELP = ("Это бот уведомлений «Учёт прибытия» АО «Каражанбасмунай».\n\n"
        "Чтобы получать уведомления, откройте сайт, нажмите на своё имя вверху и «Подключить Telegram».")


class Command(BaseCommand):
    help = "Запустить Telegram-бота уведомлений (long polling)"

    def handle(self, *args, **opts):
        if not settings.TELEGRAM_ENABLED:
            self.stderr.write("TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_USERNAME не заданы — бот не запущен. Жду настройки.")
            while True:  # не падать в цикл перезапусков docker; после правки .env контейнер перезапускают
                time.sleep(3600)
        me = telegram.api("getMe")
        self.stdout.write(f"Бот @{me['username']} запущен.")
        offset = None
        while True:
            try:
                updates = telegram.api("getUpdates", http_timeout=70, timeout=50, offset=offset,
                                       allowed_updates=["message"])
            except telegram.TelegramError as e:
                logger.warning("getUpdates: %s", e)
                time.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                close_old_connections()
                try:
                    self.on_message(update.get("message") or {})
                except Exception:
                    logger.exception("Ошибка обработки сообщения Telegram")

    def on_message(self, message):
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return
        chat_id, text = str(chat["id"]), (message.get("text") or "").strip()
        if text.startswith("/start"):
            user = telegram.user_by_token(text[len("/start"):].strip())
            if user is None:
                linked = User.objects.filter(telegram_chat_id=chat_id, is_active=True).first()
                reply = (f"Вы уже подключены как {linked}." if linked else
                         HELP + "\n\nЕсли вы нажимали ссылку больше суток назад, получите новую на сайте.")
                telegram.send_message(chat_id, reply)
                return
            # один Telegram — один аккаунт: отвязываем его от прежнего владельца
            User.objects.filter(telegram_chat_id=chat_id).exclude(pk=user.pk).update(telegram_chat_id="")
            user.telegram_chat_id, user.telegram_token, user.telegram_token_at = chat_id, "", None
            user.save(update_fields=["telegram_chat_id", "telegram_token", "telegram_token_at"])
            log_action(None, AuditLog.Action.USER_UPDATE, user, {"telegram": "подключён"}, user=user)
            role = User.Role.ADMIN if user.is_admin_role else user.role
            what = WHAT_YOU_GET.get(role, "уведомления по заявкам")
            telegram.send_message(chat_id, f"Готово, {user.display_name}! Telegram подключён.\n\nСюда будут приходить: {what}."
                                           "\n\nОтключить: /stop")
        elif text.startswith("/stop"):
            users = list(User.objects.filter(telegram_chat_id=chat_id))
            for user in users:
                user.telegram_chat_id = ""
                user.save(update_fields=["telegram_chat_id"])
                log_action(None, AuditLog.Action.USER_UPDATE, user, {"telegram": "отключён"}, user=user)
            telegram.send_message(chat_id, "Уведомления отключены." if users else HELP)
        else:
            telegram.send_message(chat_id, HELP)
