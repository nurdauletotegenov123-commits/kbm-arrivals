"""Уведомления службам на почту о новых/изменённых/отменённых заявках.

ИИН и номера документов в письма не попадают. Если EMAIL_HOST не задан,
письма печатаются в консоль контейнера.
"""
import logging

from django.conf import settings
from django.core.mail import send_mass_mail
from django.db import transaction
from django.urls import reverse

from accounts.models import User

logger = logging.getLogger(__name__)

EVENTS = {
    "create": "Новая заявка",
    "update": "Изменена заявка",
    "cancel": "Отменена заявка",
    "resubmit": "Исправлена и снова отправлена заявка",
    "approve": "Согласована заявка",
    "reject": "Отклонена заявка",
}


def _line(v):
    parts = [f"{v.full_name} ({v.organization})", f"{v.arrival_date:%d.%m}–{v.departure_date:%d.%m}"]
    if v.arrival_time:
        parts.append(f"≈{v.arrival_time:%H:%M}")
    if v.vehicle_plate:
        parts.append(f"авто {v.vehicle_plate}")
    if v.needs_transport:
        parts.append("нужен транспорт")
    if v.needs_camp:
        parts.append("кэмп")
    if v.needs_meals:
        parts.append("питание: " + v.meals_display)
    if v.status == v.Status.CANCELLED:
        parts.append("ОТМЕНЁН")
    return " · ".join(parts)


def notify_request(req, event):
    if not settings.NOTIFICATIONS_ENABLED:
        return
    transaction.on_commit(lambda: _send(req.pk, event))


def _recipient_roles(req, event, visitors):
    """Пока заявка не согласована, о ней знает только начальник штаба.
    Службы узнают о заявке в момент согласования — для них это «новая заявка»."""
    R = User.Role
    if req.is_pending:
        return [R.CHIEF] if event in ("create", "resubmit") else []
    if not req.is_approved or event == "reject":
        return []
    roles = [R.SECURITY]
    if any(v.needs_camp or v.needs_meals for v in visitors):
        roles.append(R.CAMP)
    if any(v.needs_transport for v in visitors):
        roles.append(R.DISPATCHER)
    return roles


def _author_message(req):
    author = req.author
    if not (author.is_active and author.notify_by_email and author.email):
        return []
    if req.is_approved:
        lines = [f"Заявка №{req.number} ({req.purpose}) согласована начальником штаба безопасности."]
        struck = [v.full_name for v in req.visitors.all() if v.status == v.Status.REJECTED]
        if struck:
            lines.append("Не допущены: " + ", ".join(struck))
    else:
        lines = [f"Заявка №{req.number} ({req.purpose}) отклонена.", f"Причина: {req.reject_reason}", "",
                 "Исправьте заявку и сохраните её — она снова уйдёт на согласование."]
    lines += ["", f"Открыть: {settings.SITE_URL.rstrip('/')}{reverse('request_detail', args=[req.pk])}"]
    subject = f"[КБМ Прибытие] Заявка №{req.number} {'согласована' if req.is_approved else 'отклонена'}"
    return [(subject, "\n".join(lines), settings.DEFAULT_FROM_EMAIL, [author.email])]


def _send(request_pk, event):
    from .models import Request

    try:
        req = Request.objects.get(pk=request_pk)
        visitors = [v for v in req.visitors.select_related("organization") if v.status != v.Status.REJECTED]
        messages = _author_message(req) if event in ("approve", "reject") else []
        roles = _recipient_roles(req, event, visitors)
        users = User.objects.filter(is_active=True, notify_by_email=True, role__in=roles).exclude(email="") if roles else []
        R = User.Role
        links = {
            R.CHIEF: reverse("request_detail", args=[req.pk]),
            R.SECURITY: reverse("kpp"),
            R.CAMP: reverse("camp"),
            R.DISPATCHER: reverse("dispatch"),
        }
        title = EVENTS["create"] if event == "approve" else EVENTS[event]
        for user in users:
            shown = visitors
            if user.role == R.CAMP:
                shown = [v for v in visitors if v.needs_camp or v.needs_meals]
            elif user.role == R.DISPATCHER:
                shown = [v for v in visitors if v.needs_transport]
            body = "\n".join(
                [
                    f"{title} №{req.number}",
                    f"Цель визита: {req.purpose}",
                    f"Ответственный: {req.contact_name}, {req.contact_phone}",
                    "",
                    *[f"• {_line(v)}" for v in shown],
                    "",
                    f"Открыть: {settings.SITE_URL.rstrip('/')}{links[user.role]}",
                ]
            )
            subject = f"[КБМ Прибытие] {title} №{req.number} ({len(shown)} чел.)"
            messages.append((subject, body, settings.DEFAULT_FROM_EMAIL, [user.email]))
        if messages:
            send_mass_mail(messages, fail_silently=False)
    except Exception:  # почта не должна ломать работу с заявками
        logger.exception("Не удалось отправить уведомление по заявке %s", request_pk)


TRIP_EVENTS = {"assign": "Назначен транспорт", "update": "Изменён транспорт", "cancel": "Отменён транспорт"}


def trip_text(trip):
    """Строки о машине для письма заявителю."""
    when = trip.date.strftime("%d.%m.%Y") + (f" в {trip.time:%H:%M}" if trip.time else "")
    lines = [f"{trip.get_direction_display()}: {when}"]
    if trip.pickup_place:
        lines.append(f"Место подачи: {trip.pickup_place}")
    lines.append(f"Машина: {trip.vehicle_make} {trip.vehicle_plate}")
    lines.append(f"Водитель: {trip.driver_name}" + (f", {trip.driver_phone}" if trip.driver_phone else ""))
    if trip.comment:
        lines.append(f"Комментарий: {trip.comment}")
    return lines


def notify_trip(trip, people, event):
    """Письмо авторам заявок, чьих людей посадили в машину (или изменили/отменили её).
    Текст собирается сразу — после удаления рейса его уже не прочитать."""
    if not settings.NOTIFICATIONS_ENABLED or not people:
        return
    by_request = {}
    for v in people:
        by_request.setdefault(v.request_id, []).append(v)
    messages = []
    for request_pk, group in by_request.items():
        req = group[0].request
        author = req.author
        if not (author.is_active and author.notify_by_email and author.email):
            continue
        body = "\n".join([
            f"{TRIP_EVENTS[event]} по заявке №{req.number} ({req.purpose})",
            "",
            *trip_text(trip),
            "",
            "Едут: " + ", ".join(v.full_name for v in group),
            "",
            f"Открыть: {settings.SITE_URL.rstrip('/')}{reverse('request_detail', args=[request_pk])}",
        ])
        subject = f"[КБМ Прибытие] {TRIP_EVENTS[event]}: заявка №{req.number}"
        messages.append((subject, body, settings.DEFAULT_FROM_EMAIL, [author.email]))
    if messages:
        transaction.on_commit(lambda: _send_trip(messages))


def _send_trip(messages):
    try:
        send_mass_mail(messages, fail_silently=False)
    except Exception:  # почта не должна ломать работу диспетчера
        logger.exception("Не удалось отправить письмо о транспорте")
