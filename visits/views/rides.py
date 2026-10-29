"""Заявки на машину: заявитель просит машину, начальник штаба согласует поездки на месторождение,
диспетчер назначает машину."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_POST

from accounts.decorators import role_required

from .. import bans, rides
from ..forms import ReasonForm, RideForm
from ..models import RidePassenger, RideRequest
from ..services import ActionError
from .common import R

S = RideRequest.Status
P = RideRequest.Approval


def _rides():
    return RideRequest.objects.select_related("author", "from_point", "to_point", "trip", "approved_by").prefetch_related(
        Prefetch("passengers", queryset=RidePassenger.objects.all()))


@role_required(R.APPLICANT)
def ride_list(request):
    show = request.GET.get("show", "current")
    today = timezone.localdate()
    qs = _rides().filter(author=request.user)
    qs = qs.filter(date__gte=today).order_by("date", "time") if show == "current" else qs.filter(date__lt=today)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "visits/ride_list.html", {"page": page, "show": show})


@role_required(R.APPLICANT)
def ride_create(request):
    user = request.user
    form = RideForm(request.POST or None, initial={
        "date": timezone.localdate(), "contact_phone": user.phone, "passengers": user.full_name,
    })
    if request.method == "POST" and form.is_valid():
        ride = rides.create_ride(form.cleaned_data, user, request=request)
        if ride.approval == P.PENDING:
            messages.success(request, _("Поездка №%(n)s отправлена на согласование начальнику штаба безопасности.")
                             % {"n": ride.number})
        else:
            messages.success(request, _("Поездка №%(n)s отправлена диспетчеру.") % {"n": ride.number})
        return redirect("ride_detail", pk=ride.pk)
    return render(request, "visits/ride_form.html", {"form": form})


def _can_see(user, ride):
    return ride.author_id == user.pk or user.has_role(R.CHIEF, R.DISPATCHER)


@role_required(R.APPLICANT, R.CHIEF, R.DISPATCHER)
def ride_detail(request, pk):
    user = request.user
    ride = get_object_or_404(_rides(), pk=pk)
    if not _can_see(user, ride):
        raise PermissionDenied
    people = list(ride.passengers.all())
    is_chief = user.has_role(R.CHIEF)
    if is_chief:
        bans.mark(people, [ride.trip] if ride.trip else [])
    entered = bool(ride.trip and ride.trip.entered_at)
    return render(request, "visits/ride_detail.html", {
        "ride": ride, "people": people, "reason_form": ReasonForm(),
        "show_docs": ride.author_id == user.pk or is_chief,  # диспетчеру ИИН не нужен
        "can_cancel": ride.author_id == user.pk and ride.is_open and not entered,
        "can_decide": is_chief and ride.approval == P.PENDING and ride.is_open,
        "can_revoke": is_chief and ride.approval == P.APPROVED and ride.is_open and not entered,
        "can_dispatch": user.has_role(R.DISPATCHER) and ride.is_open and ride.approval in (P.NOT_NEEDED, P.APPROVED),
        "has_ban": any(getattr(p, "ban", None) for p in people),
        "return_leg": getattr(ride, "return_leg", None), "is_chief": is_chief,
    })


ACTIONS = {
    # действие: (кто может, функция, нужна причина, сообщение)
    "approve": ((R.CHIEF,), rides.approve_ride, False, gettext_lazy("Поездка №%(n)s согласована.")),
    "reject": ((R.CHIEF,), rides.reject_ride, True, gettext_lazy("Поездка №%(n)s отклонена.")),
    "done": ((R.DISPATCHER,), rides.finish_ride, False, gettext_lazy("Поездка №%(n)s выполнена.")),
    "refuse": ((R.DISPATCHER,), rides.refuse_ride, True, gettext_lazy("По поездке №%(n)s отказано.")),
    "unassign": ((R.DISPATCHER,), rides.unassign_ride, False, gettext_lazy("Поездка №%(n)s снята с машины.")),
}


@require_POST
@role_required(R.APPLICANT, R.CHIEF, R.DISPATCHER)
def ride_action(request, pk, action):
    ride = get_object_or_404(RideRequest, pk=pk)
    user = request.user
    try:
        if action == "cancel":
            if ride.author_id != user.pk:
                raise PermissionDenied
            rides.cancel_ride(ride, user, request=request)
            messages.info(request, _("Поездка №%(n)s отменена.") % {"n": ride.number})
            return redirect("ride_detail", pk=pk)
        roles, func, needs_reason, text = ACTIONS[action]
        if not user.has_role(*roles):
            raise PermissionDenied
        if needs_reason:
            func(ride, user, request.POST.get("reason", ""), request=request)
        else:
            func(ride, user, request=request)
    except ActionError as e:
        messages.warning(request, str(e))
        return redirect("ride_detail", pk=pk)
    messages.success(request, str(text) % {"n": ride.number})
    if action in ("approve", "reject"):
        return redirect(f"{reverse('approval_list')}?tab=rides")
    if action in ("done", "refuse", "unassign"):
        return redirect(f"{reverse('dispatch')}?tab=ride")
    return redirect("ride_detail", pk=pk)


def pending_rides(user):
    """Для начальника штаба: поездки на месторождение на согласовании."""
    return _rides().filter(approval=P.PENDING).exclude(status=S.CANCELLED).order_by("date", "time")


def recent_decided_rides():
    return _rides().filter(Q(approval=P.APPROVED) | Q(approval=P.REJECTED)).order_by("-approved_at")[:50]
