"""Заявки на машину: создание, согласование начальником штаба, назначение машины диспетчером.
Все смены статусов — здесь, чтобы каждая попала в журнал и в уведомления."""
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from audit.models import AuditLog
from audit.utils import log_action

from .models import RidePassenger, RideRequest, Trip
from .notifications import notify_ride
from .services import ActionError

A = AuditLog.Action
P = RideRequest.Approval
S = RideRequest.Status


def _snapshot(ride):
    return {"маршрут": ride.route, "дата": ride.date.strftime("%d.%m.%Y"),
            "время": ride.time.strftime("%H:%M") if ride.time else None,
            "пассажиров": ride.passengers.count(), "груз": ride.cargo or None}


@transaction.atomic
def create_ride(data, user, request=None):
    """Создать поездку (и обратную, если нужна). Возвращает поездку «туда»."""
    def make(frm, frm_addr, to, to_addr, day, time, return_of=None):
        ride = RideRequest(author=user, from_point=frm, from_address="" if frm else frm_addr.strip(),
                           to_point=to, to_address="" if to else to_addr.strip(), date=day, time=time,
                           cargo=data.get("cargo", "").strip(), contact_phone=data["contact_phone"].strip(),
                           comment=data.get("comment", "").strip(), return_of=return_of)
        ride.approval = P.PENDING if ride.to_guarded else P.NOT_NEEDED
        ride.save()
        RidePassenger.objects.bulk_create(RidePassenger(ride=ride, full_name=n, doc_number=d) for n, d in data["people"])
        log_action(request, A.RIDE_CREATE, ride, _snapshot(ride), user=user)
        notify_ride(ride, "create")
        return ride

    there = make(data["from_point"], data.get("from_address", ""), data["to_point"], data.get("to_address", ""),
                 data["date"], data["time"])
    if data.get("with_return"):
        make(data["to_point"], data.get("to_address", ""), data["from_point"], data.get("from_address", ""),
             data["return_date"], data["return_time"], return_of=there)
    return there


def _locked(ride):
    return RideRequest.objects.select_for_update().get(pk=ride.pk)


@transaction.atomic
def approve_ride(ride, user, request=None):
    ride = _locked(ride)
    if ride.approval != P.PENDING or not ride.is_open:
        raise ActionError(_("Поездка №%(n)s уже не ждёт согласования.") % {"n": ride.number})
    ride.approval, ride.approved_by, ride.approved_at = P.APPROVED, user, timezone.now()
    ride.save(update_fields=["approval", "approved_by", "approved_at", "updated_at"])
    log_action(request, A.RIDE_APPROVE, ride, {"approval": [P.PENDING, P.APPROVED]}, user=user)
    notify_ride(ride, "approve")
    return ride


@transaction.atomic
def reject_ride(ride, user, reason, request=None):
    """Начальник штаба отклоняет поездку на месторождение (или отзывает согласование до въезда)."""
    reason = (reason or "").strip()
    if not reason:
        raise ActionError(_("Укажите причину — её увидит заявитель."))
    ride = _locked(ride)
    if ride.approval not in (P.PENDING, P.APPROVED) or not ride.is_open:
        raise ActionError(_("Поездку №%(n)s уже нельзя отклонить.") % {"n": ride.number})
    if ride.trip_id and ride.trip.entered_at:
        raise ActionError(_("Машина уже въехала на территорию."))
    before = ride.approval
    ride.approval, ride.approved_by, ride.approved_at, ride.reason = P.REJECTED, user, timezone.now(), reason
    ride.trip = None
    ride.status = S.NEW
    ride.save()
    log_action(request, A.RIDE_REJECT, ride, {"approval": [before, P.REJECTED], "причина": reason}, user=user)
    notify_ride(ride, "reject")
    return ride


@transaction.atomic
def cancel_ride(ride, user, request=None):
    ride = _locked(ride)
    if not ride.is_open:
        raise ActionError(_("Поездку №%(n)s уже нельзя отменить.") % {"n": ride.number})
    if ride.trip_id and ride.trip.entered_at:
        raise ActionError(_("Машина уже в пути — позвоните диспетчеру."))
    had_trip = ride.trip_id
    ride.status, ride.trip = S.CANCELLED, None
    ride.save(update_fields=["status", "trip", "updated_at"])
    log_action(request, A.RIDE_CANCEL, ride, user=user)
    if had_trip or ride.approval != P.PENDING:
        notify_ride(ride, "cancel")  # диспетчеру: машину можно снять
    return ride


@transaction.atomic
def assign_rides(trip, ride_ids, user, request=None):
    """Посадить поездки в машину. `trip` без pk — новая машина (направление «Поездка»)."""
    is_new = trip.pk is None
    if not is_new:
        trip = Trip.objects.select_for_update().get(pk=trip.pk, direction=Trip.Direction.RIDE)
        if trip.exited_at:
            raise ActionError(_("Машина уже отмечена на выезде — добавить поездки нельзя."))
    rides = list(RideRequest.objects.for_dispatcher().select_for_update()
                 .filter(pk__in=ride_ids, status=S.NEW, date=trip.date).select_related("from_point", "to_point"))
    if not rides:
        raise ActionError(_("Выберите поездки на дату %(d)s.") % {"d": trip.date.strftime("%d.%m.%Y")})
    if is_new:
        trip.direction, trip.created_by = Trip.Direction.RIDE, user
        if not trip.pickup_place:
            trip.pickup_place = rides[0].pickup_place[:300]
        if not trip.time:
            trip.time = min((r.time for r in rides if r.time), default=None)
    trip.cargo = "\n".join(dict.fromkeys(filter(None, [trip.cargo] + [r.cargo for r in rides])))
    trip.save()
    for r in rides:
        r.trip, r.status = trip, S.ASSIGNED
        r.save(update_fields=["trip", "status", "updated_at"])
    log_action(request, A.TRIP_CREATE if is_new else A.TRIP_UPDATE, trip,
               {"поездки": [f"№{r.number} {r.route}" for r in rides]}, user=user)
    for r in rides:
        notify_ride(r, "assign")
    return trip, rides


@transaction.atomic
def unassign_ride(ride, user, request=None):
    ride = _locked(ride)
    if not ride.trip_id or ride.trip.entered_at:
        raise ActionError(_("Поездку нельзя снять с машины."))
    trip = ride.trip
    ride.trip, ride.status = None, S.NEW
    ride.save(update_fields=["trip", "status", "updated_at"])
    log_action(request, A.TRIP_UPDATE, trip, {"убрана поездка": f"№{ride.number}"}, user=user)
    notify_ride(ride, "unassign", trip=trip)
    return ride


@transaction.atomic
def finish_ride(ride, user, request=None):
    ride = _locked(ride)
    if ride.status != S.ASSIGNED:
        raise ActionError(_("Выполненной можно отметить только поездку с назначенной машиной."))
    ride.status = S.DONE
    ride.save(update_fields=["status", "updated_at"])
    log_action(request, A.RIDE_DONE, ride, user=user)
    return ride


@transaction.atomic
def refuse_ride(ride, user, reason, request=None):
    """Диспетчер не может дать машину (нет свободных и т.п.)."""
    reason = (reason or "").strip()
    if not reason:
        raise ActionError(_("Укажите причину — её увидит заявитель."))
    ride = _locked(ride)
    if not ride.is_open:
        raise ActionError(_("Поездка №%(n)s уже закрыта.") % {"n": ride.number})
    ride.status, ride.reason, ride.trip = S.REFUSED, reason, None
    ride.save(update_fields=["status", "reason", "trip", "updated_at"])
    log_action(request, A.RIDE_REFUSE, ride, {"причина": reason}, user=user)
    notify_ride(ride, "refuse")
    return ride
