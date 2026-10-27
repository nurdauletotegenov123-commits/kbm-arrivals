from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from audit.models import AuditLog
from audit.utils import diff_fields, log_action

from .. import excel, services
from ..forms import CargoForm, ImportForm, NewVisitorFormSet, RejectForm, RequestForm, VisitorFormSet
from ..models import Organization, Request, Trip, Visitor
from ..notifications import notify_request
from .common import R

A = AuditLog.Action
SNAPSHOT_FIELDS = [
    "full_name", "doc_type", "doc_number", "position", "arrival_date", "arrival_time", "departure_date", "one_day",
    "vehicle_make", "vehicle_plate", "needs_transport", "needs_camp", "needs_meals", "breakfast", "lunch", "dinner",
]


def _snapshot(obj, fields):
    data = {}
    for name in fields:
        value = getattr(obj, name)
        data[name] = value if isinstance(value, (bool, type(None))) else str(value)
    if isinstance(obj, Visitor):
        data["organization"] = str(obj.organization) if obj.organization_id else None
    return data


def _requests_qs(user):
    qs = Request.objects.select_related("author").prefetch_related(
        Prefetch("visitors", queryset=Visitor.objects.select_related("organization", "request", "arrival_trip", "departure_trip")),
        "cargo_trips",
    )
    return qs if user.has_role(R.CHIEF) else qs.filter(author=user)


@role_required(R.APPLICANT)
def request_list(request):
    qs = _requests_qs(request.user)
    show = request.GET.get("show", "current")
    q = request.GET.get("q", "").strip()
    today = timezone.localdate()
    if show == "current":
        qs = qs.filter(is_cancelled=False, visitors__departure_date__gte=today).distinct()
    elif show == "past":
        qs = qs.filter(is_cancelled=False).exclude(visitors__departure_date__gte=today)
    elif show == "cancelled":
        qs = qs.filter(is_cancelled=True)
    if q:
        qs = qs.filter(Q(pk__in=Visitor.objects.search(q).values("request_id")) | Q(purpose__icontains=q))
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "visits/request_list.html", {"page": page, "show": show, "q": q})


@role_required(R.APPLICANT, R.CHIEF)
def request_detail(request, pk):
    user = request.user
    req = get_object_or_404(_requests_qs(user), pk=pk)
    visitors = list(req.visitors.all())
    cargo = req.cargo_trip
    history = AuditLog.objects.filter(
        Q(object_type="request", object_id=str(req.pk))
        | Q(object_type="visitor", object_id__in=[str(v.pk) for v in visitors])
        | Q(object_type="trip", object_id=str(cargo.pk) if cargo else "-")
    ).select_related("user")[:200]
    return render(request, "visits/request_detail.html", {
        "req": req, "visitors": visitors, "history": history, "cargo": cargo,
        # Начальник штаба смотрит и согласует, но не правит чужие заявки.
        "can_edit": req.can_be_edited and user.has_role(R.APPLICANT) and (req.author_id == user.pk or user.is_admin_role),
        "can_decide": req.is_pending and not req.is_cancelled and user.has_role(R.CHIEF),
        "show_docs": True,  # сюда попадают только автор, начальник штаба и администратор
        "reject_form": RejectForm(),
    })


@role_required(R.APPLICANT)
def request_create(request):
    user = request.user
    req = Request(author=user, contact_name=user.full_name, contact_phone=user.phone)
    return _request_form(request, req)


@role_required(R.APPLICANT)
def request_edit(request, pk):
    req = get_object_or_404(_requests_qs(request.user), pk=pk)
    if not req.can_be_edited:
        messages.error(request, _("Отменённую заявку нельзя изменить."))
        return redirect("request_detail", pk=req.pk)
    return _request_form(request, req)


def _request_form(request, req):
    is_new = req.pk is None
    visitors_qs = Visitor.objects.none() if is_new else req.visitors.exclude(status__in=Visitor.GONE)
    data = request.POST if request.method == "POST" else None
    form = RequestForm(data, instance=req)
    formset = VisitorFormSet(data, instance=req, queryset=visitors_qs.select_related("organization"))
    cargo_trip = req.cargo_trip
    cargo_before = services._trip_snapshot(cargo_trip, services.CARGO_FIELDS) if cargo_trip else None
    cargo_form = CargoForm(data, instance=cargo_trip or Trip(direction=Trip.Direction.CARGO))
    if request.method == "POST" and all([form.is_valid(), formset.is_valid(), cargo_form.is_valid()]):
        req_before = _snapshot(req, RequestForm.Meta.fields) if not is_new else {}
        with transaction.atomic():
            req = form.save()
            if is_new:
                log_action(request, A.REQUEST_CREATE, req, _snapshot(req, RequestForm.Meta.fields))
            elif form.has_changed():
                log_action(request, A.REQUEST_UPDATE, req, diff_fields(req_before, _snapshot(req, RequestForm.Meta.fields)))
            _save_visitors(request, req, formset)
            if cargo_form.wanted:
                services.save_cargo(req, cargo_form.instance, cargo_before, request.user, request=request)
            elif cargo_trip and not cargo_form.is_locked:
                services.delete_cargo(cargo_trip, request.user, request=request)
            resubmitted = not is_new and services.resubmit_if_rejected(req, request.user, request=request)
        notify_request(req, "create" if is_new else "resubmit" if resubmitted else "update")
        if is_new or resubmitted:
            messages.success(request, _("Заявка №%(n)s отправлена на согласование начальнику штаба безопасности.")
                             % {"n": req.number})
        else:
            messages.success(request, _("Заявка №%(n)s сохранена.") % {"n": req.number})
        return redirect("request_detail", pk=req.pk)

    if request.method != "POST" and is_new:
        org = request.user.organization
        formset = NewVisitorFormSet(instance=req, queryset=visitors_qs,
                                    initial=[{"organization_name": org}] if org else None)
    return render(request, "visits/request_form.html", {
        "form": form,
        "formset": formset,
        "cargo_form": cargo_form,
        "req": req,
        "is_new": is_new,
        "organizations": Organization.objects.values_list("name", flat=True),
        "import_form": ImportForm(),
    })


def _save_visitors(request, req, formset):
    for f in formset.forms:
        instance = f.instance
        if not instance.pk and not f.has_changed():
            continue
        if f.cleaned_data.get("DELETE"):
            if instance.pk:
                instance.status = Visitor.Status.CANCELLED
                instance.save(update_fields=["status"])
                log_action(request, A.VISITOR_CANCEL, instance)
            continue
        if instance.pk and not f.has_changed():
            continue
        before = _snapshot(Visitor.objects.get(pk=instance.pk), SNAPSHOT_FIELDS) if instance.pk else None
        visitor = f.save(commit=False)
        visitor.request = req
        visitor.save()
        after = _snapshot(visitor, SNAPSHOT_FIELDS)
        if before is None:
            log_action(request, A.VISITOR_ADD, visitor, {k: v for k, v in after.items() if k != "doc_number"})
        else:
            changes = diff_fields(before, after)
            if "doc_number" in changes:
                changes["doc_number"] = ["••••", "••••"]  # не храним ИИН в журнале
            if changes:
                log_action(request, A.VISITOR_UPDATE, visitor, changes)


@require_POST
@role_required(R.APPLICANT)
def request_cancel(request, pk):
    req = get_object_or_404(_requests_qs(request.user), pk=pk)
    cancelled = services.cancel_request(req, request.user, request)
    notify_request(req, "cancel")
    if cancelled:
        messages.success(request, _("Отменено людей: %(n)s.") % {"n": len(cancelled)})
    else:
        messages.info(request, _("Отменять некого: все уже отмечены на КПП."))
    return redirect("request_detail", pk=req.pk)


@require_POST
@role_required(R.APPLICANT)
def import_parse(request):
    form = ImportForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse({"rows": [], "errors": [e for errs in form.errors.values() for e in errs]}, status=400)
    rows, errors = excel.parse_people(form.cleaned_data["file"])
    log_action(request, A.IMPORT, object_repr=f"{form.cleaned_data['file'].name}: {len(rows)} чел.")
    return JsonResponse({"rows": rows, "errors": errors})


@role_required(R.APPLICANT)
def import_template(request):
    return excel.import_template()


@role_required(R.ADMIN)
def dashboard(request):
    """Обзор для администратора."""
    today = timezone.localdate()
    active = Visitor.objects.ready()
    stats = {
        "pending": Request.objects.filter(approval=Request.Approval.PENDING, is_cancelled=False).count(),
        "expected": active.filter(arrival_date=today, status=Visitor.Status.EXPECTED).count(),
        "arrived_today": active.filter(arrived_at__date=today).count(),
        "on_site": active.filter(status=Visitor.Status.ARRIVED).count(),
        "no_show": active.no_shows(today).count(),
        "overdue": active.filter(status=Visitor.Status.ARRIVED, departure_date__lt=today).count(),
        "in_camp": active.filter(camp_status=Visitor.CampStatus.CHECKED_IN).count(),
        "camp_today": active.filter(needs_camp=True, arrival_date=today).count(),
        "meals_today": sum(1 for v in active.filter(needs_meals=True, arrival_date__lte=today, departure_date__gte=today)
                           .select_related("request") if v.eats_on(today)),
        "tomorrow": active.filter(arrival_date=today + timedelta(days=1)).count(),
        "no_transport": Visitor.objects.approved().filter(needs_transport=True, arrival_trip__isnull=True, status=Visitor.Status.EXPECTED,
                                      arrival_date__range=(today, today + timedelta(days=1))).count(),
    }
    recent = _requests_qs(request.user)[:10]
    return render(request, "visits/dashboard.html", {"stats": stats, "recent": recent, "today": today})
