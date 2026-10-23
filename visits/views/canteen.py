from datetime import date, timedelta

from django.shortcuts import render
from django.utils import timezone

from accounts.decorators import role_required

from ..excel import meal_rows
from ..models import Visitor
from .common import R


def _parse_date(value, default):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return default


@role_required(R.CAMP)
def canteen(request):
    today = timezone.localdate()
    start = _parse_date(request.GET.get("from"), today)
    end = start + timedelta(days=6)
    day = _parse_date(request.GET.get("day"), start)
    lo, hi = min(start, day), max(end, day)
    visitors = list(
        Visitor.objects.with_meals()
        .filter(arrival_date__lte=hi, departure_date__gte=lo)
        .select_related("request", "organization")
        .order_by("full_name")
    )
    rows = []
    for d, b, l, dn, n, people in meal_rows(visitors, start, end):
        rows.append({"date": d, "breakfast": b, "lunch": l, "dinner": dn, "people": n, "total": b + l + dn,
                     "no_show": sum(1 for v in people if v.is_no_show)})
    _, b, l, dn, n, people = meal_rows(visitors, day, day)[0]
    return render(request, "visits/canteen.html", {
        "rows": rows, "today": today, "start": start, "day": day,
        "prev": start - timedelta(days=7), "next": start + timedelta(days=7),
        "day_summary": {"breakfast": b, "lunch": l, "dinner": dn, "people": n},
        "people": people,
    })
