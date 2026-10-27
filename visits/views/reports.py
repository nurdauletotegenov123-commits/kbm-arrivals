from django.contrib import messages
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.decorators import role_required
from audit.models import AuditLog
from audit.utils import log_action

from .. import excel
from ..forms import PeriodForm
from ..models import Visitor
from .common import R

REPORTS = {
    "movements": (_("Прибытие и убытие (КПП)"), (R.SECURITY,)),
    "meals": (_("Сводка по питанию"), (R.CAMP,)),
    "camp": (_("Проживание в кэмпе"), (R.CAMP,)),
}


@role_required(R.SECURITY, R.CAMP)
def reports(request):
    user = request.user
    available = [(key, title) for key, (title, roles) in REPORTS.items() if user.has_role(*roles)]
    today = timezone.localdate()
    form = PeriodForm(request.GET or None, initial={"date_from": today.replace(day=1), "date_to": today})
    kind = request.GET.get("kind")
    if kind in dict(available) and form.is_valid():
        d1, d2 = form.cleaned_data["date_from"], form.cleaned_data["date_to"]
        base = Visitor.objects.select_related("request", "organization", "arrived_by", "departed_by",
                                              "arrived_checkpoint", "departed_checkpoint")
        overlap = Q(arrival_date__lte=d2, departure_date__gte=d1)
        log_action(request, AuditLog.Action.EXPORT, object_repr=f"{REPORTS[kind][0]}: {d1:%d.%m.%Y}–{d2:%d.%m.%Y}")
        if kind == "movements":
            qs = base.ready().filter(
                Q(arrival_date__range=(d1, d2)) | Q(arrived_at__date__range=(d1, d2)) | Q(departed_at__date__range=(d1, d2))
            ).order_by("arrival_date", "full_name")
            return excel.report_movements(list(qs), d1, d2, user.can_see_documents)
        if kind == "meals":
            return excel.report_meals(list(base.with_meals().filter(overlap).order_by("full_name")), d1, d2)
        return excel.report_camp(list(base.with_camp().filter(overlap).order_by("arrival_date", "full_name")), d1, d2)
    if kind and not form.is_valid():
        messages.error(request, _("Проверьте период."))
    return render(request, "visits/reports.html", {"form": form, "available": available})
