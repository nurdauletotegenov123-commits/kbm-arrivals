from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext as _

from .models import User

R = User.Role


def nav(request):
    """Пункты меню в зависимости от роли пользователя."""
    user = request.user
    if not user.is_authenticated:
        return {}
    items = []

    def add(roles, url_name, label):
        if user.has_role(*roles):
            items.append({"url": reverse(url_name), "label": label})

    add([R.ADMIN], "dashboard", _("Обзор"))
    if user.role == R.CHIEF:  # у администратора свой «Обзор»
        add([R.CHIEF], "monitor", _("Обстановка"))
    add([R.CHIEF], "approval_list", _("Согласование"))
    if user.role == R.CHIEF:
        add([R.CHIEF], "people_search", _("Проверка"))
        add([R.CHIEF], "ban_list", _("Запрет въезда"))
    add([R.APPLICANT], "request_list", _("Заявки"))
    if user.role == R.APPLICANT:  # у администратора заявки на машину видны через «Транспорт» и «Согласование»
        add([R.APPLICANT], "ride_list", _("Машина"))
    add([R.DISPATCHER], "dispatch", _("Транспорт"))
    add([R.SECURITY], "kpp", _("КПП"))
    add([R.CAMP], "camp", _("Кэмп"))
    add([R.CAMP], "canteen", _("Столовая"))
    add([R.SECURITY, R.CAMP, R.CHIEF], "reports", _("Отчёты"))
    add([R.ADMIN], "user_list", _("Пользователи"))
    add([R.ADMIN], "audit_list", _("Журнал"))
    current = request.path
    for item in items:
        item["active"] = current.startswith(item["url"])
    return {"nav_items": items, "telegram_enabled": settings.TELEGRAM_ENABLED, "role_label": user.get_role_display() if not user.is_superuser else _("Администратор")}
