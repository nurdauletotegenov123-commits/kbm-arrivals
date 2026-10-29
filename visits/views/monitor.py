"""Начальник штаба безопасности: обстановка на месторождении, проверка человека, запреты въезда."""
from collections import OrderedDict
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from audit.models import AuditLog

from .. import bans, services
from ..forms import BanForm
from ..models import EntryBan, Request, RideRequest, Trip, Visitor
from .common import R

S = Visitor.Status
A = AuditLog.Action
KPP_ACTIONS = [A.ARRIVE, A.DEPART, A.UNDO_MARK, A.TRIP_ENTER, A.TRIP_EXIT, A.TRIP_UNDO]


@role_required(R.CHIEF)
def monitor(request):
    today = timezone.localdate()
    ready = Visitor.objects.ready().select_related("organization", "request", "arrived_checkpoint", "arrival_trip")
    on_site = list(ready.filter(status=S.ARRIVED).order_by("organization__name", "full_name"))
    expected = list(ready.filter(arrival_date=today, status=S.EXPECTED).order_by("arrival_time", "full_name"))
    no_shows = list(ready.no_shows(today))
    trips_inside = list(Trip.objects.filter(entered_at__isnull=False, exited_at__isnull=True)
                        .select_related("request").order_by("entered_at"))
    cargo_today = list(Trip.objects.filter(direction=Trip.Direction.CARGO, date=today, request__is_cancelled=False,
                                           request__approval=Request.Approval.APPROVED)
                       .select_related("request").prefetch_related("files").order_by("time"))
    bans.mark(on_site + expected + no_shows, trips_inside + cargo_today)

    by_org = OrderedDict()
    for v in on_site:
        by_org.setdefault(v.organization, []).append(v)
    active = Visitor.objects.active()
    stats = {
        "on_site": len(on_site),
        "trips_inside": len(trips_inside),
        "expected": len(expected),
        "arrived_today": active.filter(arrived_at__date=today).count(),
        "no_shows": len(no_shows),
        "overdue": sum(1 for v in on_site if v.departure_date < today),
        "cargo_today": len(cargo_today),
        "pending": Request.objects.filter(approval=Request.Approval.PENDING, is_cancelled=False).count()
        + RideRequest.objects.filter(approval=RideRequest.Approval.PENDING).exclude(
            status=RideRequest.Status.CANCELLED).count(),
        "waiting_car": Visitor.objects.approved().filter(
            needs_transport=True, arrival_trip__isnull=True, status=S.EXPECTED,
            arrival_date__range=(today, today + timedelta(days=1))).count(),
    }
    alerts = [v for v in on_site + expected + no_shows if v.ban] + [t for t in trips_inside + cargo_today if t.ban]
    checkpoints = (active.filter(Q(arrived_at__date=today) | Q(departed_at__date=today))
                   .values("arrived_checkpoint__name")
                   .annotate(arrived=Count("pk", filter=Q(arrived_at__date=today)),
                             departed=Count("pk", filter=Q(departed_at__date=today)))
                   .order_by("arrived_checkpoint__name"))
    feed = AuditLog.objects.filter(action__in=KPP_ACTIONS).select_related("user")[:25]
    return render(request, "visits/monitor.html", {
        "today": today, "stats": stats, "by_org": by_org, "on_site": on_site, "expected": expected,
        "no_shows": no_shows, "trips_inside": trips_inside, "cargo_today": cargo_today, "alerts": alerts,
        "checkpoints": checkpoints, "feed": feed, "now": timezone.localtime(),
    })


@role_required(R.CHIEF)
def people_search(request):
    """Проверка человека или машины: все визиты за всё время и запреты въезда."""
    q = request.GET.get("q", "").strip()
    page, found_bans = None, []
    if q:
        qs = (Visitor.objects.search(q)
              .select_related("organization", "request", "arrived_checkpoint", "departed_checkpoint", "arrived_by")
              .order_by("-arrival_date", "full_name"))
        page = Paginator(qs, 50).get_page(request.GET.get("page"))
        bans.mark(page.object_list)
        doc = q.replace(" ", "").upper()
        found_bans = EntryBan.objects.filter(Q(full_name__icontains=q) | Q(doc_number=doc) | Q(vehicle_plate=doc))
    return render(request, "visits/people_search.html", {"q": q, "page": page, "found_bans": found_bans})


@role_required(R.CHIEF)
def ban_list(request):
    initial = {k: request.GET[k] for k in ("full_name", "doc_number", "vehicle_plate") if request.GET.get(k)}
    form = BanForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        ban = services.add_ban(form.save(commit=False), request.user, request=request)
        messages.success(request, _("Запрет въезда добавлен: %(n)s.") % {"n": ban})
        return redirect("ban_list")
    show = request.GET.get("show", "active")
    qs = EntryBan.objects.select_related("created_by", "lifted_by")
    if show != "all":
        qs = qs.filter(is_active=True)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "visits/ban_list.html", {"form": form, "page": page, "show": show,
                                                    "open_form": bool(initial) or form.is_bound})


@require_POST
@role_required(R.CHIEF)
def ban_lift(request, pk):
    ban = get_object_or_404(EntryBan, pk=pk)
    try:
        services.lift_ban(ban, request.user, request=request)
        messages.info(request, _("Запрет снят: %(n)s.") % {"n": ban})
    except services.ActionError as e:
        messages.warning(request, str(e))
    return redirect("ban_list")
