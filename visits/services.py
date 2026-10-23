"""Смена статусов людей. Все отметки идут только через эти функции,
чтобы каждая попадала в журнал действий."""
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from audit.models import AuditLog
from audit.utils import log_action

from .models import Trip, Visitor
from .notifications import notify_trip

S = Visitor.Status
D = Trip.Direction
C = Visitor.CampStatus
A = AuditLog.Action


class ActionError(Exception):
    """Действие недопустимо в текущем статусе (например, двойное нажатие)."""


def _fmt(dt):
    return timezone.localtime(dt).strftime("%d.%m.%Y %H:%M") if dt else None


def locked_visitor(pk):
    """Получить человека с блокировкой строки — защита от одновременных нажатий."""
    return Visitor.objects.select_for_update().select_related("request", "organization").get(pk=pk)


@transaction.atomic
def mark_arrived(pk, user, request=None):
    v = locked_visitor(pk)
    if v.is_cancelled:
        raise ActionError(_("Визит отменён."))
    if v.status != S.EXPECTED:
        raise ActionError(_("%(name)s уже отмечен(а): %(status)s.") % {"name": v, "status": v.get_status_display()})
    v.status = S.ARRIVED
    v.arrived_at = timezone.now()
    v.arrived_by = user
    v.arrived_checkpoint = user.checkpoint
    v.save()
    changes = {"status": [S.EXPECTED, S.ARRIVED], "время": _fmt(v.arrived_at)}
    if v.arrived_checkpoint:
        changes["КПП"] = v.arrived_checkpoint.name
    if v.arrival_date != timezone.localdate():
        changes["плановая дата"] = v.arrival_date.strftime("%d.%m.%Y")
    log_action(request, A.ARRIVE, v, changes, user=user)
    return v


@transaction.atomic
def mark_departed(pk, user, request=None):
    v = locked_visitor(pk)
    if v.status != S.ARRIVED:
        raise ActionError(_("Отметить убытие можно только у прибывшего человека."))
    v.status = S.DEPARTED
    v.departed_at = timezone.now()
    v.departed_by = user
    v.departed_checkpoint = user.checkpoint
    v.save()
    log_action(request, A.DEPART, v, {"status": [S.ARRIVED, S.DEPARTED], "время": _fmt(v.departed_at)}, user=user)
    return v


@transaction.atomic
def undo_mark(pk, user, request=None):
    """Отменить последнюю отметку КПП (ошиблись человеком)."""
    v = locked_visitor(pk)
    if v.status == S.DEPARTED:
        v.status = S.ARRIVED
        v.departed_at = v.departed_by = v.departed_checkpoint = None
        change = [S.DEPARTED, S.ARRIVED]
    elif v.status == S.ARRIVED:
        v.status = S.EXPECTED
        v.arrived_at = v.arrived_by = v.arrived_checkpoint = None
        change = [S.ARRIVED, S.EXPECTED]
    else:
        raise ActionError(_("Нечего отменять."))
    v.save()
    log_action(request, A.UNDO_MARK, v, {"status": change}, user=user)
    return v


def _room_changes(v, building, room):
    building, room = (building or "").strip()[:50], (room or "").strip()[:50]
    changes = {}
    if building != v.camp_building:
        changes["корпус"] = [v.camp_building, building]
    if room != v.camp_room:
        changes["комната"] = [v.camp_room, room]
    v.camp_building, v.camp_room = building, room
    return changes


@transaction.atomic
def camp_check_in(pk, user, building="", room="", request=None):
    v = locked_visitor(pk)
    if not v.needs_camp or v.is_cancelled:
        raise ActionError(_("Человеку не заказано проживание."))
    if v.camp_status != C.WAITING:
        raise ActionError(_("%(name)s уже: %(status)s.") % {"name": v, "status": v.get_camp_status_display()})
    changes = _room_changes(v, building, room)
    v.camp_status = C.CHECKED_IN
    v.checked_in_at = timezone.now()
    v.save()
    changes["camp_status"] = [C.WAITING, C.CHECKED_IN]
    log_action(request, A.CAMP_CHECK_IN, v, changes, user=user)
    return v


@transaction.atomic
def camp_check_out(pk, user, request=None):
    v = locked_visitor(pk)
    if v.camp_status != C.CHECKED_IN:
        raise ActionError(_("Выселить можно только заселённого."))
    v.camp_status = C.CHECKED_OUT
    v.checked_out_at = timezone.now()
    v.save()
    log_action(request, A.CAMP_CHECK_OUT, v, {"camp_status": [C.CHECKED_IN, C.CHECKED_OUT]}, user=user)
    return v


@transaction.atomic
def camp_set_room(pk, user, building, room, request=None):
    v = locked_visitor(pk)
    if not v.needs_camp:
        raise ActionError(_("Человеку не заказано проживание."))
    changes = _room_changes(v, building, room)
    if changes:
        v.save()
        log_action(request, A.CAMP_ROOM, v, changes, user=user)
    return v


@transaction.atomic
def camp_undo(pk, user, request=None):
    v = locked_visitor(pk)
    if v.camp_status == C.CHECKED_OUT:
        v.camp_status, v.checked_out_at = C.CHECKED_IN, None
        change = [C.CHECKED_OUT, C.CHECKED_IN]
    elif v.camp_status == C.CHECKED_IN:
        v.camp_status, v.checked_in_at = C.WAITING, None
        change = [C.CHECKED_IN, C.WAITING]
    else:
        raise ActionError(_("Нечего отменять."))
    v.save()
    log_action(request, A.CAMP_ROOM, v, {"camp_status": change, "действие": "отмена отметки"}, user=user)
    return v


@transaction.atomic
def cancel_request(req, user, request=None):
    """Отменить всех ещё не прибывших. Если никто не приезжал — заявка отменяется целиком."""
    cancelled = []
    for v in req.visitors.select_for_update().filter(status=S.EXPECTED):
        v.status = S.CANCELLED
        v.save(update_fields=["status"])
        cancelled.append(v.full_name)
    if not req.visitors.filter(status__in=[S.ARRIVED, S.DEPARTED]).exists():
        req.is_cancelled = True
        req.save(update_fields=["is_cancelled", "updated_at"])
    log_action(request, A.REQUEST_CANCEL, req, {"отменены": cancelled}, user=user)
    return cancelled


# --- транспорт (диспетчер и КПП) ---

TRIP_FIELDS = ["time", "pickup_place", "vehicle_make", "vehicle_plate", "driver_name", "driver_phone", "comment"]


def _trip_snapshot(trip):
    return {name: (str(getattr(trip, name)) if getattr(trip, name) is not None else None) for name in TRIP_FIELDS}


def _trip_field(direction):
    return "arrival_trip" if direction == D.IN else "departure_trip"


def _assignable(trip, visitor_ids):
    """Люди, которых можно посадить в эту машину: нужен транспорт, дата совпадает с рейсом,
    на привоз — ещё не прибыли, на вывоз — ещё не убыли."""
    qs = Visitor.objects.active().select_for_update().filter(pk__in=visitor_ids, needs_transport=True)
    if trip.is_in:
        qs = qs.filter(arrival_date=trip.date, status=S.EXPECTED)
    else:
        qs = qs.filter(departure_date=trip.date, status__in=[S.EXPECTED, S.ARRIVED])
    return list(qs)


@transaction.atomic
def assign_trip(trip, visitor_ids, user, request=None):
    """Посадить людей в машину. `trip` без pk — новая машина, создаётся здесь."""
    is_new = trip.pk is None
    if not is_new:
        trip = Trip.objects.select_for_update().get(pk=trip.pk)
        if trip.exited_at or (trip.is_in and trip.entered_at):
            raise ActionError(_("Машина уже отмечена на КПП — добавить людей нельзя."))
    people = _assignable(trip, visitor_ids)
    if not people:
        raise ActionError(_("Выберите людей на дату %(d)s.") % {"d": trip.date.strftime("%d.%m.%Y")})
    if is_new:
        trip.created_by = user
        trip.save()
    for v in people:
        setattr(v, _trip_field(trip.direction), trip)
        v.save(update_fields=[_trip_field(trip.direction)])
    changes = {"пассажиры": [v.full_name for v in people]}
    if is_new:
        changes.update(_trip_snapshot(trip))
    log_action(request, A.TRIP_CREATE if is_new else A.TRIP_UPDATE, trip, changes, user=user)
    notify_trip(trip, people, "assign")
    return trip, people


@transaction.atomic
def update_trip(trip, before, user, request=None):
    """Сохранить изменения машины/водителя. `before` — снимок до правки."""
    trip.save()
    changes = {k: [before.get(k), v] for k, v in _trip_snapshot(trip).items() if before.get(k) != v}
    if changes:
        log_action(request, A.TRIP_UPDATE, trip, changes, user=user)
        if set(changes) - {"comment"}:  # время, место, машина или водитель — заявителю важно знать
            notify_trip(trip, list(trip.passengers().select_related("request__author")), "update")
    return trip


@transaction.atomic
def remove_passenger(trip, visitor_pk, user, request=None):
    v = locked_visitor(visitor_pk)
    field = _trip_field(trip.direction)
    if getattr(v, f"{field}_id") != trip.pk:
        raise ActionError(_("Этого человека нет в машине."))
    if trip.is_in and v.status != S.EXPECTED:
        raise ActionError(_("%(name)s уже прибыл(а) — убрать из машины нельзя.") % {"name": v})
    setattr(v, field, None)
    v.save(update_fields=[field])
    log_action(request, A.TRIP_UPDATE, trip, {"убран": v.full_name}, user=user)
    return v


@transaction.atomic
def delete_trip(trip, user, request=None):
    trip = Trip.objects.select_for_update().get(pk=trip.pk)
    if trip.is_marked:
        raise ActionError(_("Машина уже отмечена на КПП — удалить рейс нельзя."))
    people = list(trip.passengers().select_related("request__author"))
    names = [v.full_name for v in people]
    log_action(request, A.TRIP_DELETE, trip, {**_trip_snapshot(trip), "пассажиры": names}, user=user)
    notify_trip(trip, people, "cancel")
    trip.delete()  # пассажиры снова «без машины» (SET_NULL)
    return names


@transaction.atomic
def trip_enter(pk, user, request=None):
    trip = Trip.objects.select_for_update().get(pk=pk)
    if trip.entered_at:
        raise ActionError(_("Машина %(plate)s уже въехала.") % {"plate": trip.vehicle_plate})
    trip.entered_at, trip.entered_by, trip.entered_checkpoint = timezone.now(), user, user.checkpoint
    trip.save()
    log_action(request, A.TRIP_ENTER, trip, {"время": _fmt(trip.entered_at)}, user=user)
    return trip


@transaction.atomic
def trip_exit(pk, user, request=None):
    trip = Trip.objects.select_for_update().get(pk=pk)
    if trip.exited_at:
        raise ActionError(_("Машина %(plate)s уже выехала.") % {"plate": trip.vehicle_plate})
    if not trip.entered_at:
        raise ActionError(_("Сначала отметьте въезд машины."))
    trip.exited_at, trip.exited_by, trip.exited_checkpoint = timezone.now(), user, user.checkpoint
    trip.save()
    log_action(request, A.TRIP_EXIT, trip, {"время": _fmt(trip.exited_at)}, user=user)
    return trip


@transaction.atomic
def trip_undo(pk, user, request=None):
    trip = Trip.objects.select_for_update().get(pk=pk)
    if trip.exited_at:
        trip.exited_at = trip.exited_by = trip.exited_checkpoint = None
        what = "выезд"
    elif trip.entered_at:
        trip.entered_at = trip.entered_by = trip.entered_checkpoint = None
        what = "въезд"
    else:
        raise ActionError(_("Нечего отменять."))
    trip.save()
    log_action(request, A.TRIP_UNDO, trip, {"отменён": what}, user=user)
    return trip


def trip_passengers_arrived(pk, user, request=None):
    """Одной кнопкой отметить «Прибыл» всех ещё не отмеченных пассажиров машины на привоз."""
    trip = Trip.objects.get(pk=pk, direction=D.IN)
    done = []
    for v in trip.passengers().filter(status=S.EXPECTED):
        done.append(mark_arrived(v.pk, user, request=request))
    return trip, done
