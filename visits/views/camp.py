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

from .. import services
from ..models import Visitor
from .common import R, pop_action, redirect_back, remember_action

S = Visitor.Status
C = Visitor.CampStatus


def _base():
    return Visitor.objects.with_camp().select_related("request", "organization")


def _waiting_first(people):
    """Сначала те, кого надо заселить: уже прошли КПП (кто раньше прошёл — выше),
    потом ещё не прибывшие, в самом конце — уже заселённые."""
    def key(v):
        if v.camp_status != C.WAITING:
            return (2, 0)
        if v.status == S.ARRIVED:
            return (0, v.arrived_at.timestamp() if v.arrived_at else 0)
        return (1, 0)  # порядок по времени прибытия сохраняется (сортировка устойчивая)

    return sorted(people, key=key)


@role_required(R.CAMP)
def camp(request):
    today = timezone.localdate()
    tab = request.GET.get("tab", "today")
    q = request.GET.get("q", "").strip()
    base = _base()

    arriving = base.filter(arrival_date=today).exclude(camp_status=C.CHECKED_OUT)
    # прибыли раньше (или опоздали), но ещё не заселены
    late_arrived = base.filter(arrival_date__lt=today, camp_status=C.WAITING, status=S.ARRIVED)
    no_shows = base.no_shows(today).filter(camp_status=C.WAITING)
    leaving = base.filter(camp_status=C.CHECKED_IN).filter(Q(departure_date__lte=today) | Q(status=S.DEPARTED))
    living = base.filter(camp_status=C.CHECKED_IN).order_by("camp_building", "camp_room", "full_name")
    counts = {
        "arriving": arriving.count() + late_arrived.count(),
        "waiting": arriving.filter(camp_status=C.WAITING).count() + late_arrived.count(),
        "leaving": leaving.count(),
        "living": living.count(),
        "no_shows": no_shows.count(),
        "tomorrow": base.filter(arrival_date=today + timedelta(days=1)).count(),
    }

    sections, days = [], []
    if q:
        tab = "search"
        found = base.search(q).filter(
            Q(camp_status=C.CHECKED_IN)
            | Q(arrival_date__gte=today - timedelta(days=settings.NO_SHOW_DAYS), arrival_date__lte=today + timedelta(days=30))
        ).exclude(camp_status=C.CHECKED_OUT, checked_out_at__date__lt=today)[:50]
        sections.append({"title": _("Найдено"), "items": list(found), "empty": _("Никого не найдено.")})
    elif tab == "living":
        sections.append({"title": _("Сейчас проживают"), "items": list(living), "empty": _("Никто не проживает.")})
    elif tab == "upcoming":
        horizon = int(request.GET.get("days", 7)) if request.GET.get("days", "7").isdigit() else 7
        horizon = max(1, min(horizon, 31))
        upcoming = list(base.filter(arrival_date__gt=today, arrival_date__lte=today + timedelta(days=horizon))
                        .order_by("arrival_date", "arrival_time", "full_name"))
        departures = list(base.filter(departure_date__gt=today, departure_date__lte=today + timedelta(days=horizon))
                          .exclude(camp_status=C.CHECKED_OUT))
        for i in range(1, horizon + 1):
            day = today + timedelta(days=i)
            days.append({
                "date": day,
                "items": [v for v in upcoming if v.arrival_date == day],
                "leaving": sum(1 for v in departures if v.departure_date == day),
            })
    else:
        tab = "today"
        sections.append({"title": _("Заезжают сегодня"), "items": _waiting_first(list(late_arrived) + list(arriving)),
                         "empty": _("Сегодня заездов нет.")})
        if counts["no_shows"]:
            sections.append({"title": _("Не прибыли в назначенный день"), "items": list(no_shows), "warn": True})
        sections.append({"title": _("Выезжают сегодня"), "items": list(leaving), "empty": _("Сегодня выездов нет.")})

    return render(request, "visits/camp.html", {
        "tab": tab, "q": q, "sections": sections, "days": days, "counts": counts, "today": today,
        "last_action": pop_action(request),
    })


@require_POST
@role_required(R.CAMP)
def camp_action(request, pk, action):
    pk = int(pk)
    building, room = request.POST.get("building", ""), request.POST.get("room", "")
    try:
        if action == "check_in":
            v = services.camp_check_in(pk, request.user, building, room, request=request)
            text = _("%(name)s — заселён(а)") % {"name": v.full_name}
            if v.room_display:
                text += f", {v.room_display}"
        elif action == "check_out":
            v = services.camp_check_out(pk, request.user, request=request)
            text = _("%(name)s — выселен(а)") % {"name": v.full_name}
        elif action == "room":
            v = services.camp_set_room(pk, request.user, building, room, request=request)
            messages.success(request, _("%(name)s: комната %(room)s.") % {"name": v.full_name, "room": v.room_display or "—"})
            return redirect_back(request, "camp", f"v{pk}")
        else:  # undo
            v = services.camp_undo(pk, request.user, request=request)
            messages.info(request, _("%(name)s: отметка отменена.") % {"name": v.full_name})
            return redirect_back(request, "camp", f"v{pk}")
    except Visitor.DoesNotExist:
        messages.error(request, _("Человек не найден."))
        return redirect_back(request, "camp")
    except services.ActionError as e:
        messages.warning(request, str(e))
        return redirect_back(request, "camp", f"v{pk}")
    remember_action(request, v, text, reverse("camp_action", args=[v.pk, "undo"]))
    return redirect_back(request, "camp")
