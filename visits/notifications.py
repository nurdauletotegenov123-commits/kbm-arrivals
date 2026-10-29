"""Уведомления о заявках и транспорте: на почту и в Telegram.

Каждому получателю — одно сообщение: письмо, если указан e-mail и стоит галочка «получать уведомления»,
и сообщение в Telegram, если он подключил бота. ИИН и номера документов в уведомления не попадают.
Если EMAIL_HOST не задан, письма печатаются в консоль контейнера.
"""
import logging

from django.conf import settings
from django.core.mail import send_mass_mail
from django.db import transaction
from django.db.models import Q
from django.urls import reverse

from accounts import telegram
from accounts.models import User

logger = logging.getLogger(__name__)

EVENTS = {
    "create": "Новая заявка",
    "update": "Изменена заявка",
    "cancel": "Отменена заявка",
    "resubmit": "Исправлена и снова отправлена заявка",
    "approve": "Согласована заявка",
    "reject": "Отклонена заявка",
    "revoke": "Отозвано согласование заявки",
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


def notify_request(req, event, report=None):
    """`report` — для изменений: {pk: {"name", "lines": [(текст, {роли})]}} из формы заявки.
    Тогда каждая служба получает только касающиеся её строки «было → стало»."""
    if not settings.NOTIFICATIONS_ENABLED:
        return
    transaction.on_commit(lambda: _send(req.pk, event, report))


def _recipient_roles(req, event, visitors):
    """Пока заявка не согласована, о ней знает только начальник штаба.
    Службы узнают о заявке в момент согласования — для них это «новая заявка»."""
    R = User.Role
    if req.is_pending:
        return [R.CHIEF] if event in ("create", "resubmit") else []
    if event == "revoke":
        pass  # службам надо знать, что людей больше не ждут
    elif not req.is_approved or event == "reject":
        return []
    roles = [R.SECURITY]
    if any(v.needs_camp or v.needs_meals for v in visitors):
        roles.append(R.CAMP)
    if any(v.needs_transport for v in visitors):
        roles.append(R.DISPATCHER)
    return roles


def _reachable(qs):
    """Пользователи, которым есть куда написать."""
    return qs.filter(is_active=True).filter(Q(notify_by_email=True) & ~Q(email="") | ~Q(telegram_chat_id=""))


def _can_reach(user):
    return user.is_active and ((user.notify_by_email and user.email) or user.telegram_chat_id)


def _deliver(notes):
    """notes: [(user, тема, текст)]. Текст начинается с заголовка, поэтому в Telegram уходит только он."""
    emails = [(f"[КБМ Прибытие] {subject}", body, settings.DEFAULT_FROM_EMAIL, [user.email])
              for user, subject, body in notes if user.notify_by_email and user.email]
    if emails:
        try:
            send_mass_mail(emails, fail_silently=False)
        except Exception:  # почта не должна ломать работу
            logger.exception("Не удалось отправить письма")
    for user, _subject, body in notes:
        if user.telegram_chat_id:
            telegram.send_message(user.telegram_chat_id, body)


def _author_message(req, event):
    author = req.author
    if not _can_reach(author):
        return []
    if req.is_approved:
        lines = [f"Заявка №{req.number} ({req.purpose}) согласована начальником штаба безопасности."]
        struck = [v.full_name for v in req.visitors.all() if v.status == v.Status.REJECTED]
        if struck:
            lines.append("Не допущены: " + ", ".join(struck))
    else:
        what = "согласование отозвано" if event == "revoke" else "отклонена"
        lines = [f"Заявка №{req.number} ({req.purpose}): {what}.", f"Причина: {req.reject_reason}", "",
                 "Исправьте заявку и сохраните её — она снова уйдёт на согласование."]
    lines += ["", f"Открыть: {settings.SITE_URL.rstrip('/')}{reverse('request_detail', args=[req.pk])}"]
    subject = f"Заявка №{req.number}: " + ("согласована" if req.is_approved else
                                           "согласование отозвано" if event == "revoke" else "отклонена")
    return [(author, subject, "\n".join(lines))]


def _cargo_lines(req, role):
    """Груз нужен начальнику (согласовать) и охране (пропустить машину)."""
    cargo = req.cargo_trip
    if not cargo or role not in (User.Role.CHIEF, User.Role.SECURITY):
        return []
    files = cargo.files.count()
    return ["", f"Груз: {cargo.cargo}", f"Договор: {cargo.contract_number or '—'}",
            f"Машина: {cargo.vehicle_make} {cargo.vehicle_plate}, {cargo.date:%d.%m.%Y}"
            + (f", документов: {files}" if files else "")]


def _title(event, role):
    """Заголовок под задачу получателя: начальнику — согласовать, диспетчеру — дать машину."""
    R = User.Role
    if role == R.CHIEF and event == "create":
        return "Новая заявка на согласование"
    if event == "approve":
        return "Назначьте машину: заявка" if role == R.DISPATCHER else EVENTS["create"]
    return EVENTS[event]


def _changes_for(report, role):
    """Строки изменений, важные для роли: ["• Иванов Иван: выезд 30.10 → 02.11; питание снято", ...]."""
    out = []
    for item in report.values():
        texts = [text for text, roles in item["lines"] if role in roles]
        if texts:
            out.append(f"• {item['name']}: " + "; ".join(texts))
    return out


def _send_changes(req, report):
    """Изменения согласованной заявки: каждой службе — только то, что её касается."""
    R = User.Role
    links = {R.SECURITY: reverse("kpp"), R.CAMP: reverse("camp"), R.DISPATCHER: reverse("dispatch")}
    notes = []
    for user in _reachable(User.objects.filter(role__in=list(links))):
        lines = _changes_for(report, user.role) if report else []
        if not report and user.role == R.SECURITY:
            lines = ["• изменены данные заявки (цель, ответственный или груз)"]
        if not lines:
            continue
        dates = any("заезд" in l or "выезд" in l for l in lines)
        title = "Перенос дат" if dates else EVENTS["update"]
        body = "\n".join([
            f"{title}: заявка №{req.number}",
            f"Цель визита: {req.purpose}",
            f"Ответственный: {req.contact_name}, {req.contact_phone}",
            "",
            "Что изменилось:",
            *lines,
            "",
            f"Открыть: {settings.SITE_URL.rstrip('/')}{links[user.role]}",
        ])
        notes.append((user, f"{title}: заявка №{req.number} ({len(lines)} чел.)", body))
    _deliver(notes)


def _send(request_pk, event, report=None):
    from .models import Request

    try:
        req = Request.objects.get(pk=request_pk)
        if event == "update" and report is not None:
            if req.is_approved and not req.is_cancelled:
                _send_changes(req, report)
            return
        visitors = [v for v in req.visitors.select_related("organization") if v.status != v.Status.REJECTED]
        notes = _author_message(req, event) if event in ("approve", "reject", "revoke") else []
        roles = _recipient_roles(req, event, visitors)
        users = _reachable(User.objects.filter(role__in=roles)) if roles else []
        R = User.Role
        links = {
            R.CHIEF: reverse("request_detail", args=[req.pk]),
            R.SECURITY: reverse("kpp"),
            R.CAMP: reverse("camp"),
            R.DISPATCHER: reverse("dispatch"),
        }
        for user in users:
            title = _title(event, user.role)
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
                    *_cargo_lines(req, user.role),
                    "",
                    f"Открыть: {settings.SITE_URL.rstrip('/')}{links[user.role]}",
                ]
            )
            subject = f"{title} №{req.number} ({len(shown)} чел.)"
            notes.append((user, subject, body))
        _deliver(notes)
    except Exception:  # уведомления не должны ломать работу с заявками
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
    """Уведомление авторам заявок, чьих людей посадили в машину (или изменили/отменили её).
    Текст собирается сразу — после удаления рейса его уже не прочитать."""
    if not settings.NOTIFICATIONS_ENABLED or not people:
        return
    by_request = {}
    for v in people:
        by_request.setdefault(v.request_id, []).append(v)
    notes = []
    for request_pk, group in by_request.items():
        req = group[0].request
        author = req.author
        if not _can_reach(author):
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
        subject = f"{TRIP_EVENTS[event]}: заявка №{req.number}"
        notes.append((author, subject, body))
    if notes:
        transaction.on_commit(lambda: _deliver(notes))


# --- заявки на машину ---

RIDE_EVENTS = {
    "approve": "Поездка согласована",
    "reject": "Поездка отклонена",
    "assign": "Назначена машина",
    "update": "Изменена машина",
    "unassign": "Машина снята",
    "refuse": "Машину дать не могут",
}


def _ride_lines(ride):
    when = ride.date.strftime("%d.%m.%Y") + (f" в {ride.time:%H:%M}" if ride.time else "")
    people = [p.full_name for p in ride.passengers.all()]
    lines = [f"Маршрут: {ride.route}", f"Когда: {when}"]
    if people:
        lines.append(f"Едут ({len(people)}): " + ", ".join(people))
    if ride.cargo:
        lines.append(f"Груз: {ride.cargo}")
    lines.append(f"Заявитель: {ride.author}, {ride.contact_phone}")
    if ride.comment:
        lines.append(f"Комментарий: {ride.comment}")
    return lines


def notify_ride(ride, event, trip=None):
    """Кому и что: на согласовании — начальнику штаба; ждёт машину — диспетчеру; машина, отказ — заявителю."""
    if not settings.NOTIFICATIONS_ENABLED:
        return
    R = User.Role
    base = settings.SITE_URL.rstrip("/")
    detail = base + reverse("ride_detail", args=[ride.pk])
    notes = []
    services = []
    if event == "create" and ride.approval == ride.Approval.PENDING:
        services.append((R.CHIEF, "Поездка на месторождение на согласование", detail))
    elif event in ("create", "approve"):
        services.append((R.DISPATCHER, "Нужна машина", base + reverse("dispatch") + "?tab=ride"))
    elif event == "cancel":
        services.append((R.DISPATCHER, "Отменена поездка", base + reverse("dispatch") + "?tab=ride"))
    for role, title, link in services:
        body = "\n".join([f"{title}: №{ride.number}", "", *_ride_lines(ride), "", f"Открыть: {link}"])
        for user in _reachable(User.objects.filter(role=role)):
            notes.append((user, f"{title}: №{ride.number}, {ride.date:%d.%m}", body))

    author = ride.author
    if event in RIDE_EVENTS and _can_reach(author):
        lines = [f"{RIDE_EVENTS[event]}: поездка №{ride.number}", f"{ride.route}, {ride.date:%d.%m.%Y}", ""]
        if event in ("assign", "update") and ride.trip_id:
            lines += trip_text(ride.trip)
        elif event == "unassign":
            lines.append("Диспетчер снял машину — назначит другую и сообщит.")
        elif event in ("reject", "refuse"):
            lines.append(f"Причина: {ride.reason}")
        lines += ["", f"Открыть: {detail}"]
        notes.append((author, f"{RIDE_EVENTS[event]}: поездка №{ride.number}", "\n".join(lines)))
    if notes:
        transaction.on_commit(lambda: _deliver(notes))
