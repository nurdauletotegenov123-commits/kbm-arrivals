from datetime import date, timedelta
from io import BytesIO

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from accounts.decorators import role_required
from audit.models import AuditLog
from audit.utils import log_action
from visits import bans
from visits.services import ActionError
from visits.text import normalize_plate, normalize_search
from visits.views.common import R, redirect_back

from . import services
from .forms import DeliveryForm, ItemFormSet, UploadForm
from .importer import PlanError, apply_plan, parse_plan
from .models import Delivery, DeliveryItem
from .queries import on_day
from .queries import with_items as _qs

S = Delivery.Status
PREVIEW_KEY = "delivery_import"
EDITORS = (R.SUPPLY,)
VIEWERS = (R.SUPPLY, R.CHIEF, R.SECURITY)


def _abp_list():
    return sorted({a for a in DeliveryItem.objects.exclude(abp="").values_list("abp", flat=True)}, key=str.upper)


def _parse_day(value, fallback):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback


@role_required(*VIEWERS)
def board(request):
    today = timezone.localdate()
    day = _parse_day(request.GET.get("day"), today)
    abp = request.GET.get("abp", "")
    state = request.GET.get("state", "")
    q = request.GET.get("q", "").strip()

    qs = _qs()
    if q:
        plate = normalize_plate(q)
        qs = qs.filter(Q(search_index__contains=normalize_search(q)) | (Q(truck_plate__contains=plate) if len(plate) >= 3 else Q(pk=None)))
        qs = qs.filter(date_to__gte=today - timedelta(days=60))
    else:
        qs = on_day(qs, day)
    abp_list = _abp_list()
    if abp:  # SQLite не сравнивает кириллицу без учёта регистра — ищем написание в списке
        variants = [a for a in abp_list if a.upper() == abp.upper()] or [abp]
        qs = qs.filter(items__abp__in=variants).distinct()
    deliveries = list(qs.order_by("date_from", "truck_plate", "pk"))
    for d in deliveries:
        d.state_key, d.state_label = d.state
    counts = {"all": len(deliveries)}
    for d in deliveries:
        counts[d.state_key] = counts.get(d.state_key, 0) + 1
    counts["arrived_all"] = counts.get("arrived", 0) + counts.get("offschedule", 0)
    if state:
        deliveries = [d for d in deliveries if d.state_key == state or (state == "arrived" and d.state_key == "offschedule")]
    bans.BanIndex().mark(trips=deliveries)

    days = []
    for i in range(-1, 6):
        d = today + timedelta(days=i)
        days.append({"date": d, "count": on_day(Delivery.objects.exclude(status=S.CANCELLED), d).count(),
                     "label": _("Сегодня") if i == 0 else _("Завтра") if i == 1 else _("Вчера") if i == -1 else None})
    return render(request, "deliveries/board.html", {
        "deliveries": deliveries, "day": day, "today": today, "days": days, "abp": abp, "state": state, "q": q,
        "abp_list": abp_list, "counts": counts, "can_edit": request.user.has_role(*EDITORS),
        "items_total": sum(len(d.items.all()) for d in deliveries),
    })


@role_required(*VIEWERS)
def detail(request, pk):
    d = get_object_or_404(_qs(), pk=pk)
    d.state_key, d.state_label = d.state
    bans.BanIndex().mark(trips=[d])
    history = AuditLog.objects.filter(object_type=Delivery._meta.model_name, object_id=str(d.pk)).order_by("-created_at")[:30]
    return render(request, "deliveries/detail.html", {
        "d": d, "can_edit": request.user.has_role(*EDITORS), "can_mark": request.user.has_role(R.SECURITY),
        "history": history, "today": timezone.localdate(),
    })


def _save_form(request, instance=None):
    is_new = instance is None
    instance = instance or Delivery(source=Delivery.Source.MANUAL, created_by=request.user,
                                    date_from=timezone.localdate(), date_to=timezone.localdate())
    form = DeliveryForm(request.POST or None, instance=instance)
    formset = ItemFormSet(request.POST or None, instance=instance, prefix="items")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            d = form.save()
            formset.instance = d
            formset.save()
            d.reindex()
            log_action(request, AuditLog.Action.DELIVERY_SAVE, d,
                       {"создана" if is_new else "изменена": f"{d.truck_plate} {d.dates_display}",
                        "позиций": d.items.count()})
        messages.success(request, _("Поставка %(p)s сохранена.") % {"p": d.truck_plate})
        if request.POST.get("again"):
            return redirect(reverse("delivery_create") + f"?day={d.date_from.isoformat()}")
        return redirect("delivery_detail", d.pk)
    if is_new and request.method != "POST":
        day = _parse_day(request.GET.get("day"), None)
        if day:
            form.initial.update({"date_from": day, "date_to": day})
    return render(request, "deliveries/form.html", {
        "form": form, "formset": formset, "d": None if is_new else instance, "abp_list": _abp_list(),
    })


@role_required(*EDITORS)
def create(request):
    return _save_form(request)


@role_required(*EDITORS)
def edit(request, pk):
    d = get_object_or_404(Delivery, pk=pk)
    if d.status == S.CANCELLED:
        messages.warning(request, _("Поставка отменена — изменить нельзя."))
        return redirect("delivery_detail", pk)
    return _save_form(request, d)


@require_POST
@role_required(*EDITORS)
def cancel(request, pk):
    d = get_object_or_404(Delivery, pk=pk)
    if d.is_marked:
        messages.warning(request, _("Машина уже отмечена на КПП — отменить нельзя."))
    elif d.status != S.CANCELLED:
        d.status = S.CANCELLED
        d.save(update_fields=["status", "updated_at"])
        log_action(request, AuditLog.Action.DELIVERY_SAVE, d, {"отменена": f"{d.truck_plate} {d.dates_display}"})
        messages.info(request, _("Поставка %(p)s отменена.") % {"p": d.truck_plate or d.plate_raw})
    return redirect("delivery_detail", pk)


# ---------- загрузка Excel ----------

@role_required(*EDITORS)
def import_upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        f = form.cleaned_data["file"]
        try:
            deliveries, warnings = parse_plan(f)
        except PlanError as e:
            form.add_error("file", str(e))
        else:
            request.session[PREVIEW_KEY] = {"file": f.name, "deliveries": deliveries, "warnings": warnings}
            return redirect("delivery_import_preview")
    return render(request, "deliveries/import.html", {"form": form})


def _preview_rows(deliveries):
    """Для каждой машины из файла: новая или уже есть (обновится)."""
    rows = []
    for d in deliveries:
        lookup = Q(date_from=d["date_from"], date_to=d["date_to"])
        lookup &= Q(truck_plate=normalize_plate(d["truck_plate"])) if d["truck_plate"] else Q(plate_raw=d["plate_raw"])
        exists = Delivery.objects.filter(lookup).exclude(status=S.CANCELLED).exists()
        rows.append({**d, "exists": exists, "date_from": date.fromisoformat(d["date_from"]),
                     "date_to": date.fromisoformat(d["date_to"])})
    return rows


@role_required(*EDITORS)
def import_preview(request):
    data = request.session.get(PREVIEW_KEY)
    if not data:
        return redirect("delivery_import")
    if request.method == "POST":
        if request.POST.get("action") == "cancel":
            request.session.pop(PREVIEW_KEY, None)
            return redirect("delivery_import")
        stats = apply_plan(data["deliveries"], request.user)
        request.session.pop(PREVIEW_KEY, None)
        log_action(request, AuditLog.Action.DELIVERY_IMPORT, None,
                   {"файл": data["file"], "новых машин": stats["created"], "обновлено": stats["updated"],
                    "новых позиций": stats["items"]}, object_repr=data["file"])
        messages.success(request, _("План загружен: новых машин — %(c)s, обновлено — %(u)s, новых позиций груза — %(i)s.")
                         % {"c": stats["created"], "u": stats["updated"], "i": stats["items"]})
        first = min((d["date_from"] for d in data["deliveries"]), default=None)
        today = timezone.localdate().isoformat()
        return redirect(reverse("delivery_board") + f"?day={max(first, today) if first else today}")
    rows = _preview_rows(data["deliveries"])
    return render(request, "deliveries/import_preview.html", {
        "file": data["file"], "rows": rows, "warnings": data["warnings"],
        "new": sum(1 for r in rows if not r["exists"]), "upd": sum(1 for r in rows if r["exists"]),
        "items": sum(len(r["items"]) for r in rows),
    })


# ---------- выгрузка в Excel в их формате ----------

EXPORT_COLUMNS = [
    ("№", 5), ("АБП", 10), ("Подрядная организация", 28), ("Наименование оборудования/материала", 48),
    ("Объём", 12), ("№ договора / заказа", 22), ("Вид транспорта", 18), ("Номер авто", 20), ("Дата въезда", 14),
    ("Ф.И.О водителя", 24), ("Контакты", 22), ("ОТП", 7), ("Финальная поставка", 11), ("СТ-KZ внесены", 11),
    ("Примечание (КПП)", 28),
]


def kpp_note(d):
    key, label = d.state
    if d.entered_at:
        text = f"{label} {timezone.localtime(d.entered_at):%d.%m %H:%M}"
        if d.exited_at:
            text += f", выехал {timezone.localtime(d.exited_at):%d.%m %H:%M}"
        return text
    return str(label)


def _yn(v):
    return "" if v is None else ("да" if v else "нет")


@role_required(R.SUPPLY, R.CHIEF)
def export(request):
    today = timezone.localdate()
    d1 = _parse_day(request.GET.get("from"), today - timedelta(days=1))
    d2 = _parse_day(request.GET.get("to"), today)
    if d2 < d1:
        d1, d2 = d2, d1
    qs = _qs().filter(date_from__lte=d2, date_to__gte=d1).exclude(status=S.CANCELLED).order_by("date_from", "truck_plate")

    wb = Workbook()
    ws = wb.active
    ws.title = "Поставки"
    thin = Side(style="thin", color="B0B7BF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")
    ws.append([f"Ежедневная информация по поставке оборудования/материалов на месторождение "
               f"{d1:%d.%m.%Y}{'' if d1 == d2 else f' – {d2:%d.%m.%Y}'}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    ws.append([c for c, _w in EXPORT_COLUMNS])
    for i, (_c, width) in enumerate(EXPORT_COLUMNS, start=1):
        cell = ws.cell(row=3, column=i)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0071A9")
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cell.border = border
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A4"
    fills = {"arrived": "E3F4E8", "departed": "E8EEF3", "offschedule": "FFF1D6", "noshow": "FBE3E3"}
    n = 0
    for d in qs:
        key, _label = d.state
        plate = " / ".join(filter(None, [d.truck_plate or d.plate_raw, d.trailer_plate and f"прицеп {d.trailer_plate}"]))
        note = kpp_note(d)
        for it in d.items.all():
            n += 1
            ws.append([n, it.abp, it.supplier, it.name, it.quantity, it.contract, d.vehicle, plate, d.dates_display,
                       d.driver_name, d.contact_phone or d.contact_name, _yn(it.is_otp), _yn(it.is_final),
                       _yn(it.local_content_ok), note])
            for cell in ws[ws.max_row]:
                cell.alignment, cell.border = wrap, border
                if key in fills:
                    cell.fill = PatternFill("solid", fgColor=fills[key])
    out = BytesIO()
    wb.save(out)
    resp = HttpResponse(out.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="postavki_{d1:%Y%m%d}_{d2:%Y%m%d}.xlsx"'
    return resp


# ---------- КПП ----------

ACTIONS = {
    "enter": (services.enter, _("въехала")),
    "exit": (services.leave, _("выехала")),
    "undo": (services.undo, _("отметка отменена")),
}


@require_POST
@role_required(R.SECURITY)
def kpp_action(request, pk, action):
    pk = int(pk)
    func, label = ACTIONS[action]
    try:
        d = func(pk, request.user, request=request)
    except Delivery.DoesNotExist:
        messages.error(request, _("Поставка не найдена."))
        return redirect_back(request, "kpp")
    except ActionError as e:
        messages.warning(request, str(e))
        return redirect_back(request, "kpp", f"d{pk}")
    text = _("Груз %(plate)s — %(label)s") % {"plate": d.truck_plate or d.plate_raw, "label": label}
    if action == "undo":
        messages.info(request, text + ".")
    else:
        request.session["last_action"] = {
            "pk": d.pk, "text": f"{text} в {timezone.localtime().strftime('%H:%M')}",
            "undo_url": reverse("delivery_kpp_action", args=[d.pk, "undo"]),
        }
    return redirect_back(request, "kpp", f"d{pk}")
