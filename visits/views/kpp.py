from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from deliveries.queries import for_kpp as deliveries_for_kpp

from .. import bans, services
from ..models import Request, Trip, Visitor
from ..text import normalize_plate
from .common import R, pop_action, redirect_back, remember_action

S = Visitor.Status


def _base():
    return Visitor.objects.ready().select_related(
        "request", "organization", "arrived_by", "arrived_checkpoint", "arrival_trip", "departure_trip")


def _trips_today(today):
    """Машины диспетчера и машины с грузом на сегодня, и те, что въехали раньше и ещё не выехали."""
    return _visible_trips(Trip.objects.filter(date=today) | Trip.objects.filter(entered_at__isnull=False, exited_at__isnull=True)) \
        .distinct().order_by("exited_at", "entered_at", "time", "pk")


def _visible_trips(qs):
    """Охрана видит: машины диспетчера на привоз/вывоз; машину с грузом — по согласованной заявке;
    машину по заявкам на машину — если она едет на охраняемую территорию или забирает оттуда."""
    D = Trip.Direction
    guarded_ride = Q(direction=D.RIDE) & (Q(rides__to_point__is_guarded=True) | Q(rides__from_point__is_guarded=True))
    cargo = Q(direction=D.CARGO, request__approval=Request.Approval.APPROVED, request__is_cancelled=False)
    return qs.filter(Q(direction__in=[D.IN, D.OUT]) | cargo | guarded_ride).distinct() \
        .select_related("request").prefetch_related("files", "rides__passengers", "rides__from_point", "rides__to_point")


@role_required(R.SECURITY)
def kpp(request):
    today = timezone.localdate()
    tab = request.GET.get("tab", "expected")
    q = request.GET.get("q", "").strip()
    base = _base()

    expected = base.filter(arrival_date=today, status=S.EXPECTED)
    tomorrow = base.filter(arrival_date=today + timedelta(days=1), status=S.EXPECTED)
    no_shows = base.no_shows(today)
    on_site = base.filter(status=S.ARRIVED).order_by("departure_date", "full_name")
    departed = base.filter(status=S.DEPARTED, departed_at__date=today).order_by("-departed_at")
    counts = {
        "expected": expected.count(),
        "no_shows": no_shows.count(),
        "on_site": on_site.count(),
        "leaving": on_site.filter(departure_date__lte=today).count(),
        "departed": departed.count(),
        "tomorrow": tomorrow.count(),
        "trips": _trips_today(today).filter(exited_at__isnull=True).count(),
        "cargo": deliveries_for_kpp(today).exclude(status="departed").count(),
    }

    sections, trips, cargo = [], [], []
    if q:
        tab = "search"
        plate = normalize_plate(q)
        if len(plate) >= 3:
            trips = list(_visible_trips(Trip.objects).filter(vehicle_plate__contains=plate, date__gte=today - timedelta(days=1),
                                             date__lte=today + timedelta(days=1)))
            cargo = list(deliveries_for_kpp(today).filter(Q(truck_plate__contains=plate) | Q(trailer_plate__contains=plate)))
        window = Q(arrival_date__gte=today - timedelta(days=settings.NO_SHOW_DAYS), arrival_date__lte=today + timedelta(days=14))
        found = base.search(q).filter(window | Q(status=S.ARRIVED)).exclude(
            Q(status=S.DEPARTED) & ~Q(departed_at__date=today)
        )[:50]
        sections.append({"title": _("Найдено"), "items": list(found), "empty": _("Никого не найдено. Проверьте ФИО или госномер.")})
    elif tab == "on_site":
        sections.append({"title": _("Убывают сегодня / просрочен выезд"), "items": list(on_site.filter(departure_date__lte=today))})
        sections.append({"title": _("На территории"), "items": list(on_site.filter(departure_date__gt=today)),
                         "empty": _("Никого.")})
    elif tab == "trips":
        trips = list(_trips_today(today))
    elif tab == "cargo":
        cargo = list(deliveries_for_kpp(today))
    elif tab == "tomorrow":
        sections.append({"title": _("Ожидаются завтра"), "items": list(tomorrow),
                         "empty": _("На завтра заявок нет.")})
    elif tab == "departed":
        sections.append({"title": _("Убыли сегодня"), "items": list(departed), "empty": _("Сегодня ещё никто не убыл.")})
    else:
        tab = "expected"
        if counts["no_shows"]:
            sections.append({"title": _("Не прибыли в назначенный день"), "items": list(no_shows), "warn": True})
        sections.append({"title": _("Ожидаются сегодня"), "items": list(expected),
                         "empty": _("На сегодня больше никого не ожидается.")})

    for t in trips:
        if t.is_ride:  # пассажиры заявок на машину — с ИИН, для сверки на КПП
            t.ride_people = [p for r in t.rides.all() for p in r.passengers.all()]
            bans.mark(t.ride_people)
    bans.mark([v for s in sections for v in s["items"]], trips + cargo)
    return render(request, "visits/kpp.html", {
        "tab": tab, "q": q, "sections": sections, "trips": trips, "cargo": cargo, "counts": counts, "today": today,
        "last_action": pop_action(request), "show_docs": request.user.can_see_documents,
    })


ACTIONS = {
    "arrive": (services.mark_arrived, _("прибыл(а)")),
    "depart": (services.mark_departed, _("убыл(а)")),
    "undo": (services.undo_mark, _("отметка отменена")),
}


@require_POST
@role_required(R.SECURITY)
def kpp_action(request, pk, action):
    pk = int(pk)
    func, label = ACTIONS[action]
    try:
        v = func(pk, request.user, request=request)
    except Visitor.DoesNotExist:
        messages.error(request, _("Человек не найден."))
        return redirect_back(request, "kpp")
    except services.ActionError as e:
        messages.warning(request, str(e))
        return redirect_back(request, "kpp", f"v{pk}")
    when = timezone.localtime().strftime("%H:%M")
    if action == "undo":
        messages.info(request, f"{v.full_name}: {label}.")
    else:
        remember_action(request, v, f"{v.full_name} — {label} в {when}", reverse("kpp_action", args=[v.pk, "undo"]))
    return redirect_back(request, "kpp")


TRIP_ACTIONS = {
    "enter": (services.trip_enter, _("въехала")),
    "exit": (services.trip_exit, _("выехала")),
    "undo": (services.trip_undo, _("отметка отменена")),
}


@require_POST
@role_required(R.SECURITY)
def kpp_trip_action(request, pk, action):
    pk = int(pk)
    try:
        if action == "passengers":
            trip, done = services.trip_passengers_arrived(pk, request.user, request=request)
            messages.success(request, _("%(plate)s: отмечено прибывшими — %(n)s.") % {"plate": trip.vehicle_plate, "n": len(done)})
            return redirect_back(request, "kpp", f"t{pk}")
        func, label = TRIP_ACTIONS[action]
        trip = func(pk, request.user, request=request)
    except Trip.DoesNotExist:
        messages.error(request, _("Машина не найдена."))
        return redirect_back(request, "kpp")
    except services.ActionError as e:
        messages.warning(request, str(e))
        return redirect_back(request, "kpp", f"t{pk}")
    text = _("Машина %(plate)s — %(label)s") % {"plate": trip.vehicle_plate, "label": label}
    if action == "undo":
        messages.info(request, text + ".")
        return redirect_back(request, "kpp", f"t{pk}")
    request.session["last_action"] = {
        "pk": trip.pk, "text": f"{text} в {timezone.localtime().strftime('%H:%M')}",
        "undo_url": reverse("kpp_trip_action", args=[trip.pk, "undo"]),
    }
    return redirect_back(request, "kpp", f"t{pk}")
