"""Электронные пропуска. Ссылка открывается без входа (как электронный билет): гость видит свой пропуск
без ИИН. Охранник, вошедший на сайт, по той же ссылке видит карточку КПП с кнопками «Прибыл» / «Убыл»."""
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from audit.models import AuditLog
from audit.utils import log_action

from .. import bans, passes, services
from ..models import Visitor
from .common import R
from .requests import _requests_qs

S = Visitor.Status


def _is_guard(user):
    return user.is_authenticated and user.has_role(R.SECURITY)


def _no_index(response):
    response["X-Robots-Tag"] = "noindex, nofollow"
    # same-origin: ссылка с кодом пропуска не уходит на сторонние сайты (WhatsApp и т.п.), а кнопки «Прибыл»
    # работают. С no-referrer браузер шлёт «Origin: null» и Django отклоняет нажатие как подделку (CSRF).
    response["Referrer-Policy"] = "same-origin"
    return response


@never_cache
def pass_view(request, token):
    v = passes.visitor_by_token(token)
    if v is None:
        raise Http404
    key, text = passes.state(v)
    url = request.build_absolute_uri(reverse("pass_view", args=[token]))
    guard = _is_guard(request.user)
    ctx = {"v": v, "state": key, "state_text": text, "url": url, "guard": guard, "today": timezone.localdate()}
    if guard:
        log_action(request, AuditLog.Action.PASS_SCAN, v, {"пропуск": text})
        bans.mark([v])
        ctx.update({
            "show_docs": True,
            "on_kpp": Visitor.objects.ready().filter(pk=v.pk).exists(),  # как в списке КПП
            "group_url": reverse("group_pass", args=[v.request.pass_token]),
        })
    return _no_index(render(request, "visits/pass.html", ctx))


@never_cache
def group_pass(request, token):
    req = passes.request_by_token(token)
    if req is None:
        raise Http404
    people = [v for v in req.visitors.select_related("organization", "request", "arrival_trip")
              if v.status not in Visitor.GONE]
    for v in people:
        v.pass_state, v.pass_text = passes.state(v)
    guard = _is_guard(request.user)
    if guard:
        log_action(request, AuditLog.Action.PASS_SCAN, req, {"общий пропуск": len(people)})
        bans.mark(people)
        ready = set(Visitor.objects.ready().filter(pk__in=[v.pk for v in people]).values_list("pk", flat=True))
        for v in people:
            v.can_arrive = v.pk in ready and v.status == S.EXPECTED
    return _no_index(render(request, "visits/group_pass.html", {
        "req": req, "people": people, "guard": guard,
        "url": request.build_absolute_uri(reverse("group_pass", args=[token])),
        "can_arrive_any": guard and any(getattr(v, "can_arrive", False) for v in people),
    }))


@require_POST
@role_required(R.SECURITY)
def group_arrive(request, token):
    """Охрана отметила галочками, кто из бригады приехал, — «Прибыл» всем сразу."""
    req = passes.request_by_token(token)
    if req is None:
        raise Http404
    ids = [int(i) for i in request.POST.getlist("visitor") if i.isdigit()]
    ready = Visitor.objects.ready().filter(request=req, pk__in=ids, status=S.EXPECTED)
    done = []
    for v in ready:
        try:
            done.append(services.mark_arrived(v.pk, request.user, request=request))
        except services.ActionError as e:
            messages.warning(request, str(e))
    if done:
        messages.success(request, _("Отмечены прибывшими: %(n)s.") % {"n": len(done)})
    else:
        messages.info(request, _("Никто не отмечен."))
    return redirect("group_pass", token=token)


@role_required(R.APPLICANT, R.CHIEF)
def print_passes(request, pk):
    req = get_object_or_404(_requests_qs(request.user), pk=pk)
    if not req.is_approved:
        messages.warning(request, _("Пропуска появятся после согласования заявки начальником штаба."))
        return redirect("request_detail", pk=pk)
    people = [v for v in req.visitors.select_related("organization") if v.status not in Visitor.GONE]
    for v in people:
        v.pass_url = request.build_absolute_uri(reverse("pass_view", args=[v.pass_token]))
    return render(request, "visits/print_passes.html", {
        "req": req, "people": people,
        "group_url": request.build_absolute_uri(reverse("group_pass", args=[req.pass_token])),
    })
