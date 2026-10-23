from datetime import date

from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render

from accounts.decorators import role_required
from accounts.models import User

from .models import AuditLog


@role_required(User.Role.ADMIN)
def audit_list(request):
    qs = AuditLog.objects.select_related("user")
    f = {k: request.GET.get(k, "").strip() for k in ("q", "action", "user", "date_from", "date_to")}
    if f["q"]:
        qs = qs.filter(Q(object_repr__icontains=f["q"]) | Q(username__icontains=f["q"]) | Q(object_id=f["q"]))
    if f["action"]:
        qs = qs.filter(action=f["action"])
    if f["user"]:
        qs = qs.filter(username=f["user"])
    for key, lookup in (("date_from", "created_at__date__gte"), ("date_to", "created_at__date__lte")):
        try:
            qs = qs.filter(**{lookup: date.fromisoformat(f[key])}) if f[key] else qs
        except ValueError:
            pass
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "audit/audit_list.html", {
        "page": page, "f": f, "actions": AuditLog.Action.choices,
        "usernames": User.objects.order_by("username").values_list("username", flat=True),
    })
