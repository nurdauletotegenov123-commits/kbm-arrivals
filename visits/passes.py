"""Электронный пропуск: ссылка с кодом + QR. Гость показывает его на КПП, охрана сканирует камерой телефона."""
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Request, Visitor

S = Visitor.Status


def state(v, today=None):
    """(ключ, текст) — действует ли пропуск человека сейчас.
    ключи: valid, early, used, pending, invalid, expired."""
    today = today or timezone.localdate()
    req = v.request
    dates = f"{v.arrival_date:%d.%m}–{v.departure_date:%d.%m.%Y}"
    if v.status == S.REJECTED:
        return "invalid", _("Не согласован начальником штаба безопасности")
    if v.is_cancelled:
        return "invalid", _("Визит отменён")
    if req.is_pending:
        return "pending", _("Ждёт согласования начальником штаба безопасности")
    if not req.is_approved:
        return "invalid", _("Согласование отозвано или заявка отклонена")
    if v.status == S.DEPARTED:
        return "used", _("Убыл %(when)s") % {"when": timezone.localtime(v.departed_at).strftime("%d.%m %H:%M")}
    if v.status == S.ARRIVED:
        return "valid", _("На территории · пропуск до %(d)s") % {"d": f"{v.departure_date:%d.%m.%Y}"}
    if v.waits_arrival_trip:
        return "pending", _("Ждёт назначения машины диспетчером")
    if today < v.arrival_date:
        return "early", _("Пропуск на %(dates)s — ещё не начался") % {"dates": dates}
    if today > v.departure_date:
        return "expired", _("Срок пропуска истёк (%(dates)s)") % {"dates": dates}
    return "valid", _("Действителен %(dates)s") % {"dates": dates}


def visitor_by_token(token):
    return (Visitor.objects.select_related("request", "organization", "arrival_trip", "departure_trip",
                                           "arrived_checkpoint", "arrived_by")
            .filter(pass_token=token).first())


def request_by_token(token):
    return Request.objects.filter(pass_token=token).first()
