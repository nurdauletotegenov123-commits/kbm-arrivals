"""Уведомления в Telegram через бота. Без сторонних пакетов — только Bot API по HTTPS.

Привязка: пользователь на сайте нажимает «Подключить Telegram», получает ссылку
t.me/<бот>?start=<одноразовый код>, жмёт «Start» — бот (команда telegram_bot) узнаёт его chat_id.
"""
import json
import logging
import secrets
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)
TOKEN_TTL = timedelta(hours=24)


class TelegramError(Exception):
    pass


def api(method, http_timeout=10, **params):
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    req = urllib.request.Request(url, data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        data = json.load(e) if e.headers.get_content_type() == "application/json" else {"description": str(e)}
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise TelegramError(str(e)) from e
    if not data.get("ok"):
        raise TelegramError(data.get("description", "ошибка Telegram"))
    return data["result"]


def send_message(chat_id, text):
    """Отправить сообщение. Ошибки только пишутся в лог: Telegram не должен ломать работу с заявками."""
    if not settings.TELEGRAM_ENABLED or not chat_id:
        return False
    try:
        api("sendMessage", chat_id=chat_id, text=text[:4000], disable_web_page_preview=True)
        return True
    except TelegramError:
        logger.exception("Не удалось отправить сообщение в Telegram (chat %s)", chat_id)
        return False


def link_url(user):
    """Новая одноразовая ссылка для привязки Telegram к аккаунту."""
    user.telegram_token = secrets.token_urlsafe(16)
    user.telegram_token_at = timezone.now()
    user.save(update_fields=["telegram_token", "telegram_token_at"])
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={user.telegram_token}"


def user_by_token(token):
    from .models import User

    if not token:
        return None
    return User.objects.filter(
        telegram_token=token, telegram_token_at__gte=timezone.now() - TOKEN_TTL, is_active=True
    ).first()
