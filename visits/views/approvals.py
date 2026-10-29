"""Начальник штаба безопасности: согласует заявки до того, как их увидят диспетчер, охрана и кэмп."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from accounts.decorators import role_required

from .. import bans, services
from ..forms import RejectForm
from ..models import Request, Visitor
from .rides import pending_rides, recent_decided_rides
from ..notifications import notify_request
from .common import R
from .requests import _requests_qs

P = Request.Approval
TABS = {"pending": P.PENDING, "approved": P.APPROVED, "rejected": P.REJECTED}


@role_required(R.CHIEF)
def approval_list(request):
    tab = request.GET.get("tab", "pending")
    base = _requests_qs(request.user).filter(is_cancelled=False)
    counts = {key: base.filter(approval=value).count() for key, value in TABS.items()}
    counts["rides"] = pending_rides(request.user).count()
    if tab == "rides":
        return render(request, "visits/approval_list.html", {
            "tab": tab, "counts": counts, "rides_pending": list(pending_rides(request.user)),
            "rides_recent": list(recent_decided_rides()),
        })
    tab = tab if tab in TABS else "pending"
    q = request.GET.get("q", "").strip()
    qs = base.filter(approval=TABS[tab])
    # на согласовании — сначала самые старые, остальное — последние решения сверху
    qs = qs.order_by("created_at") if tab == "pending" else qs.order_by("-approved_at", "-created_at")
    if q:
        qs = qs.filter(Q(pk__in=Visitor.objects.search(q).values("request_id")) | Q(purpose__icontains=q))
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    index = bans.BanIndex()
    for r in page.object_list:
        r.has_ban = any(index.for_visitor(v) for v in r.visitors.all() if v.status == Visitor.Status.EXPECTED) or \
            any(index.for_trip(t) for t in r.cargo_trips.all())
    return render(request, "visits/approval_list.html", {"page": page, "tab": tab, "q": q, "counts": counts})


@require_POST
@role_required(R.CHIEF)
def request_approve(request, pk):
    req = get_object_or_404(Request, pk=pk)
    # Снимаем тех, кого начальник видел на странице, но не отметил «допустить».
    ids = lambda name: {int(i) for i in request.POST.getlist(name) if i.isdigit()}  # noqa: E731
    strike = ids("shown") - ids("admit")
    current = set(req.visitors.filter(status=Visitor.Status.EXPECTED).values_list("pk", flat=True))
    if current - ids("shown"):
        messages.warning(request, _("Заявитель только что изменил заявку. Проверьте людей ещё раз."))
        return redirect("request_detail", pk=pk)
    try:
        req, struck = services.approve_request(req, request.user, strike, request=request)
    except services.ActionError as e:
        messages.warning(request, str(e))
        return redirect("request_detail", pk=pk)
    notify_request(req, "approve")
    text = _("Заявка №%(n)s согласована.") % {"n": req.number}
    if struck:
        text += " " + _("Не допущены: %(names)s.") % {"names": ", ".join(struck)}
    messages.success(request, text)
    return redirect("approval_list")


@require_POST
@role_required(R.CHIEF)
def request_reject(request, pk):
    req = get_object_or_404(Request, pk=pk)
    form = RejectForm(request.POST)
    try:
        if not form.is_valid():
            raise services.ActionError(_("Укажите причину отказа — её увидит заявитель."))
        req = services.reject_request(req, request.user, form.cleaned_data["reason"], request=request)
    except services.ActionError as e:
        messages.warning(request, str(e))
        return redirect("request_detail", pk=pk)
    notify_request(req, "reject")
    messages.info(request, _("Заявка №%(n)s отклонена.") % {"n": req.number})
    return redirect("approval_list")


@require_POST
@role_required(R.CHIEF)
def request_revoke(request, pk):
    req = get_object_or_404(Request, pk=pk)
    form = RejectForm(request.POST)
    try:
        if not form.is_valid():
            raise services.ActionError(_("Укажите причину — её увидит заявитель."))
        req = services.revoke_approval(req, request.user, form.cleaned_data["reason"], request=request)
    except services.ActionError as e:
        messages.warning(request, str(e))
        return redirect("request_detail", pk=pk)
    notify_request(req, "revoke")
    messages.info(request, _("Согласование заявки №%(n)s отозвано.") % {"n": req.number})
    return redirect("request_detail", pk=pk)
