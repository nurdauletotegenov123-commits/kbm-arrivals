"""Главная страница портала — плитки разделов, как в eGov: каждый видит доступное ему и сколько дел ждёт."""
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import User

R = User.Role


def _count(fn):
    try:
        return fn()
    except Exception:  # счётчик на плитке не должен ломать главную
        return 0


def tiles(user):
    from permits.models import ApprovalStep
    from visits.models import Request, RideRequest

    out = []

    def add(roles, url_name, icon, title, text, badge=0, badge_text="", accent=False, query=""):
        if user.has_role(*roles):
            out.append({"url": reverse(url_name) + query, "icon": icon, "title": title, "text": text,
                        "badge": badge, "badge_text": badge_text, "accent": accent})

    if user.has_role(R.APPLICANT, R.CHIEF):
        to_sign = _count(lambda: ApprovalStep.objects.filter(approver=user, status="PENDING",
                                                             permit__status="PENDING_APPROVAL").count())
        add([R.APPLICANT, R.CHIEF], "permits_app", "permit", _("Наряд-допуски"),
            _("Наряды на опасные работы и электроустановки, подпись ЭЦП, журнал и LOTO"),
            to_sign, _("ждут вашей подписи"), accent=True)
    if user.role == R.APPLICANT or user.is_admin_role:
        mine = _count(lambda: Request.objects.filter(author=user, approval=Request.Approval.PENDING,
                                                     is_cancelled=False).count())
        add([R.APPLICANT], "request_list", "pass", _("Пропуск на месторождение"),
            _("Заявка на въезд людей, бригады и груза, пропуска с QR"), mine, _("на согласовании"))
        rides = _count(lambda: RideRequest.objects.filter(author=user, date__gte=timezone.localdate(),
                                                          status=RideRequest.Status.NEW).count())
        add([R.APPLICANT], "ride_list", "car", _("Заказать машину"),
            _("Офис Актау, склад БМТС, месторождение — людей и груз"), rides, _("ждут машину"))
    if user.has_role(R.CHIEF):
        pending = _count(lambda: Request.objects.filter(approval=Request.Approval.PENDING, is_cancelled=False).count()
                         + RideRequest.objects.filter(approval=RideRequest.Approval.PENDING)
                         .exclude(status=RideRequest.Status.CANCELLED).count())
        add([R.CHIEF], "approval_list", "shield", _("Согласование"),
            _("Заявки на въезд и поездки на месторождение"), pending, _("ждут решения"))
        add([R.CHIEF], "monitor", "pulse", _("Обстановка"), _("Кто и какие машины сейчас на территории"))
        add([R.CHIEF], "people_search", "search", _("Проверка"), _("Все визиты человека или машины"))
        add([R.CHIEF], "ban_list", "ban", _("Запрет въезда"), _("Список запрета по ИИН и госномеру"))
    if user.is_admin_role:
        add([R.ADMIN], "dashboard", "grid", _("Обзор"), _("Сводка по месторождению на сегодня"))
    add([R.DISPATCHER], "dispatch", "truck", _("Транспорт"), _("Машины на привоз, вывоз и поездки"))
    add([R.SECURITY], "kpp", "gate", _("КПП"), _("Отметки прибытия и убытия, машины"))
    add([R.CAMP], "camp", "bed", _("Кэмп"), _("Заселение, комнаты, переносы дат"))
    add([R.CAMP], "canteen", "food", _("Столовая"), _("Питание по дням"))
    add([R.SECURITY, R.CAMP, R.CHIEF], "reports", "chart", _("Отчёты"), _("Выгрузки в Excel"))
    add([R.ADMIN], "user_list", "users", _("Пользователи"), _("Роли, доступы, учётные записи"))
    add([R.ADMIN], "audit_list", "list", _("Журнал"), _("Все действия в системе"))
    return out
