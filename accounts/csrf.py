"""Понятная реакция на устаревшую форму вместо страницы «Ошибка проверки CSRF (403)».

Чаще всего это не атака: человек вошёл заново (токен формы сменился), а потом отправил форму
со старой страницы — кнопкой «Назад» или из второй вкладки. Возвращаем его на ту страницу,
откуда он пришёл, свежую, с просьбой повторить. Сам запрос не выполняется.
"""
import logging

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


def csrf_failure(request, reason=""):
    logger.warning("CSRF: %s %s (%s)", request.method, request.path, reason)
    back = request.META.get("HTTP_REFERER", "")
    if not url_has_allowed_host_and_scheme(back, {request.get_host()}, require_https=request.is_secure()):
        back = reverse("login") if request.path == reverse("login") else reverse("home")
    messages.warning(request, _("Страница устарела (вы вошли заново или она открыта в другой вкладке). "
                                "Повторите действие ещё раз."))
    return redirect(back)
