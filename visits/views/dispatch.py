"""Диспетчер транспорта: назначает машины на привоз и вывоз людей с галочкой «Нужен транспорт»."""
from datetime import timedelta

from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.decorators import role_required

from .. import services
from ..forms import TripForm
from ..models import Trip, Visitor
from .common import R

S = Visitor.Status
D = Trip.Direction
HORIZONS = (3, 7, 14)


def _waiting(direction, d1, d2):
    """Люди без машины на даты d1..d2."""
    base = Visitor.objects.active().filter(needs_transport=True).select_related("organization", "request")
    if direction == D.IN:
        return base.filter(arrival_trip__isnull=True, status=S.EXPECTED, arrival_date__range=(d1, d2))
    return base.filter(departure_trip__isnull=True, status__in=[S.EXPECTED, S.ARRIVED],
                       departure_date__range=(d1, d2)).order_by("departure_date", "full_name")


def _trips(direction, d1, d2):
    people = Visitor.objects.active().select_related("organization", "request")
    related = "arrivals" if direction == D.IN else "departures"
    return (Trip.objects.filter(direction=direction, date__range=(d1, d2))
            .prefetch_related(Prefetch(related, queryset=people, to_attr="people")))


@role_required(R.DISPATCHER)
def dispatch(request):
    today = timezone.localdate()
    direction = D.OUT if request.GET.get("tab") == "out" else D.IN
    days = request.GET.get("days", "7")
    days = int(days) if days.isdigit() and int(days) in HORIZONS else 7
    d1, d2 = today, today + timedelta(days=days)

    waiting = list(_waiting(direction, d1, d2))
    trips = list(_trips(direction, d1, d2))
    date_of = (lambda v: v.arrival_date) if direction == D.IN else (lambda v: v.departure_date)
    dates = sorted({date_of(v) for v in waiting} | {t.date for t in trips})
    groups = [{
        "date": day,
        "waiting": [v for v in waiting if date_of(v) == day],
        "trips": [t for t in trips if t.date == day],
        # в уже отмеченную на КПП машину на привоз людей не добавить
        "open_trips": [t for t in trips if t.date == day and not t.exited_at and not (t.is_in and t.entered_at)],
    } for day in dates]

    counts = {
        "in": _waiting(D.IN, d1, d2).count(),
        "out": _waiting(D.OUT, d1, d2).count(),
        "today_in": _waiting(D.IN, today, today).count(),
        "today_out": _waiting(D.OUT, today, today).count(),
    }
    return render(request, "visits/dispatch.html", {
        "tab": direction, "days": days, "horizons": HORIZONS, "groups": groups, "counts": counts,
        "today": today, "form": TripForm(prefix="new"),
        # подсказки для «Места подачи» — адреса, которые диспетчер уже вводил
        "places": list(dict.fromkeys(
            Trip.objects.exclude(pickup_place="").order_by("-pk").values_list("pickup_place", flat=True)[:200]
        ))[:30],
    })


def _back(request, trip=None):
    tab = "out" if (trip and not trip.is_in) or request.POST.get("direction") == D.OUT else "in"
    url = f"{reverse('dispatch')}?tab={tab}"
    if request.POST.get("days"):
        url += f"&days={request.POST['days']}"
    return redirect(url + (f"#t{trip.pk}" if trip and trip.pk else ""))


@require_POST
@role_required(R.DISPATCHER)
def dispatch_assign(request):
    direction = request.POST.get("direction")
    day = parse_date(request.POST.get("date") or "")
    ids = [int(i) for i in request.POST.getlist("visitor") if i.isdigit()]
    if direction not in D.values or not day:
        messages.error(request, _("Не удалось определить дату рейса. Обновите страницу."))
        return _back(request)
    if not ids:
        messages.warning(request, _("Отметьте галочками, кого посадить в машину."))
        return _back(request)

    trip_id = request.POST.get("trip")
    if trip_id:
        trip = get_object_or_404(Trip, pk=trip_id, direction=direction, date=day)
    else:
        form = TripForm(request.POST, prefix="new", instance=Trip(direction=direction, date=day))
        if not form.is_valid():
            errors = "; ".join(f"{form.fields[f].label}: {' '.join(e)}" for f, e in form.errors.items())
            messages.error(request, _("Машина не назначена — %(e)s") % {"e": errors})
            return _back(request)
        trip = form.instance
    try:
        trip, people = services.assign_trip(trip, ids, request.user, request=request)
    except services.ActionError as e:
        messages.warning(request, str(e))
        return _back(request)
    messages.success(request, _("%(plate)s: назначено людей — %(n)s.") % {"plate": trip.vehicle_plate, "n": len(people)})
    return _back(request, trip)


@role_required(R.DISPATCHER)
def trip_edit(request, pk):
    trip = get_object_or_404(Trip, pk=pk)
    before = services._trip_snapshot(trip)
    form = TripForm(request.POST or None, instance=trip)
    if request.method == "POST" and form.is_valid():
        services.update_trip(form.save(commit=False), before, request.user, request=request)
        messages.success(request, _("Рейс сохранён."))
        return _back(request, trip)
    return render(request, "visits/trip_form.html", {"trip": trip, "form": form, "people": list(trip.passengers())})


@require_POST
@role_required(R.DISPATCHER)
def trip_remove(request, pk, visitor_pk):
    trip = get_object_or_404(Trip, pk=pk)
    try:
        v = services.remove_passenger(trip, int(visitor_pk), request.user, request=request)
        messages.info(request, _("%(name)s убран(а) из машины %(plate)s.") % {"name": v.full_name, "plate": trip.vehicle_plate})
    except (services.ActionError, Visitor.DoesNotExist) as e:
        messages.warning(request, str(e))
    return redirect("trip_edit", pk=trip.pk)


@require_POST
@role_required(R.DISPATCHER)
def trip_delete(request, pk):
    trip = get_object_or_404(Trip, pk=pk)
    try:
        services.delete_trip(trip, request.user, request=request)
        messages.info(request, _("Рейс %(plate)s удалён, люди снова без машины.") % {"plate": trip.vehicle_plate})
    except services.ActionError as e:
        messages.warning(request, str(e))
        return redirect("trip_edit", pk=trip.pk)
    return _back(request, Trip(direction=trip.direction))
