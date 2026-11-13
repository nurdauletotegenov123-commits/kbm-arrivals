"""Отметки КПП по поставкам — только через эти функции, чтобы каждая попала в журнал."""
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from audit.models import AuditLog
from audit.utils import log_action
from visits.services import ActionError

from .models import Delivery

A = AuditLog.Action
S = Delivery.Status


def _locked(pk):
    return Delivery.objects.select_for_update(of=("self",)).get(pk=pk)


@transaction.atomic
def enter(pk, user, request=None):
    d = _locked(pk)
    if d.status == S.CANCELLED:
        raise ActionError(_("Поставка отменена."))
    if d.entered_at:
        raise ActionError(_("Машина %(p)s уже въехала.") % {"p": d.truck_plate or d.plate_raw})
    d.status, d.entered_at, d.entered_by, d.entered_checkpoint = S.ARRIVED, timezone.now(), user, user.checkpoint
    d.save()
    changes = {"время": timezone.localtime(d.entered_at).strftime("%d.%m.%Y %H:%M")}
    if d.is_off_schedule:
        changes["вне графика"] = d.dates_display
    log_action(request, A.DELIVERY_ENTER, d, changes, user=user)
    return d


@transaction.atomic
def leave(pk, user, request=None):
    d = _locked(pk)
    if not d.entered_at:
        raise ActionError(_("Сначала отметьте въезд."))
    if d.exited_at:
        raise ActionError(_("Машина уже выехала."))
    d.status, d.exited_at, d.exited_by, d.exited_checkpoint = S.DEPARTED, timezone.now(), user, user.checkpoint
    d.save()
    log_action(request, A.DELIVERY_EXIT, d, {"время": timezone.localtime(d.exited_at).strftime("%d.%m.%Y %H:%M")}, user=user)
    return d


@transaction.atomic
def undo(pk, user, request=None):
    d = _locked(pk)
    if d.exited_at:
        d.status, d.exited_at, d.exited_by, d.exited_checkpoint = S.ARRIVED, None, None, None
        what = "выезд"
    elif d.entered_at:
        d.status, d.entered_at, d.entered_by, d.entered_checkpoint = S.EXPECTED, None, None, None
        what = "въезд"
    else:
        raise ActionError(_("Нечего отменять."))
    d.save()
    log_action(request, A.DELIVERY_UNDO, d, {"отменён": what}, user=user)
    return d
