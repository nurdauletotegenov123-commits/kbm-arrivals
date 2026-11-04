"""Поставки оборудования и материалов на месторождение.

Единица — рейс машины («поставка»): госномер, прицеп, водитель, плановые даты въезда.
Внутри — позиции груза (АБП, поставщик, наименование, количество, договор, отметки ОТП/финальная/СТ-KZ):
в их ежедневном плане одна машина часто везёт несколько позиций и договоров.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from visits.text import normalize_plate, normalize_search


class Delivery(models.Model):
    class Source(models.TextChoices):
        EXCEL = "excel", _("Загрузка из Excel")
        MANUAL = "manual", _("Вручную")

    class Status(models.TextChoices):
        EXPECTED = "expected", _("Ожидается")
        ARRIVED = "arrived", _("Прибыл")
        DEPARTED = "departed", _("Выехал")
        CANCELLED = "cancelled", _("Отменена")

    date_from = models.DateField(_("въезд с"), db_index=True)
    date_to = models.DateField(_("въезд по"), db_index=True)
    vehicle = models.CharField(_("вид транспорта"), max_length=200, blank=True)
    truck_plate = models.CharField(_("госномер"), max_length=20, blank=True, db_index=True)
    trailer_plate = models.CharField(_("госномер прицепа"), max_length=20, blank=True)
    plate_raw = models.CharField(_("номер как в плане"), max_length=200, blank=True)
    driver_name = models.CharField(_("водитель (ФИО)"), max_length=200, blank=True)
    driver_doc = models.CharField(_("удостоверение водителя"), max_length=100, blank=True)
    contact_name = models.CharField(_("ответственное лицо"), max_length=200, blank=True)
    contact_phone = models.CharField(_("контакты"), max_length=200, blank=True)
    departure_point = models.CharField(_("пункт отправления / контакт"), max_length=200, blank=True)
    work_type = models.CharField(_("вид работы"), max_length=200, blank=True)
    note = models.CharField(_("примечание"), max_length=300, blank=True)

    status = models.CharField(_("статус"), max_length=20, choices=Status.choices, default=Status.EXPECTED, db_index=True)
    entered_at = models.DateTimeField(_("въехала"), null=True, blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    entered_checkpoint = models.ForeignKey("accounts.Checkpoint", null=True, blank=True, on_delete=models.SET_NULL,
                                           related_name="+")
    exited_at = models.DateTimeField(_("выехала"), null=True, blank=True)
    exited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    exited_checkpoint = models.ForeignKey("accounts.Checkpoint", null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name="+")
    marked_arrived_in_plan = models.BooleanField(
        _("прибыл по данным плана"), default=False,
        help_text=_("В загруженном плане уже стояло «Прибыл» — время въезда неизвестно."),
    )

    source = models.CharField(_("источник"), max_length=10, choices=Source.choices, default=Source.MANUAL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(_("создана"), auto_now_add=True)
    updated_at = models.DateTimeField(_("изменена"), auto_now=True)
    search_index = models.CharField(max_length=1000, blank=True, editable=False, db_index=True)

    class Meta:
        verbose_name = _("поставка")
        verbose_name_plural = _("поставки")
        ordering = ["date_from", "truck_plate", "pk"]

    def __str__(self):
        return f"{self.truck_plate or self.plate_raw or '—'} {self.date_from:%d.%m}"

    def save(self, *args, **kwargs):
        self.truck_plate = normalize_plate(self.truck_plate)
        self.trailer_plate = normalize_plate(self.trailer_plate)
        super().save(*args, **kwargs)

    def reindex(self):
        """Поиск по госномеру, водителю, договору, поставщику, наименованию (SQLite не ищет кириллицу без учёта регистра)."""
        parts = [self.truck_plate, self.trailer_plate, self.plate_raw, self.driver_name, self.contact_name, self.vehicle]
        for i in self.items.all():
            parts += [i.supplier, i.name, i.contract, i.abp]
        Delivery.objects.filter(pk=self.pk).update(search_index=normalize_search(" ".join(filter(None, parts)))[:1000])

    # --- для экранов ---
    @property
    def vehicle_plate(self):  # для сверки со списком запрета въезда (visits.bans)
        return self.truck_plate

    @property
    def is_marked(self):
        return bool(self.entered_at or self.exited_at)

    @property
    def is_off_schedule(self):
        """Въехала не в свои даты."""
        if not self.entered_at:
            return False
        day = timezone.localtime(self.entered_at).date()
        return not (self.date_from <= day <= self.date_to)

    @property
    def is_no_show(self):
        return self.status == self.Status.EXPECTED and not self.marked_arrived_in_plan and self.date_to < timezone.localdate()

    @property
    def state(self):
        """(ключ, текст) — статус для карточки и отчёта."""
        S = self.Status
        if self.status == S.CANCELLED:
            return "cancelled", _("Отменена")
        if self.status == S.DEPARTED:
            return "departed", _("Выехала")
        if self.status == S.ARRIVED:
            return ("offschedule", _("Прибыл вне графика")) if self.is_off_schedule else ("arrived", _("Прибыл"))
        if self.marked_arrived_in_plan:
            return "arrived", _("Прибыл (по плану)")
        if self.is_no_show:
            return "noshow", _("Не прибыл")
        return "expected", _("Ожидается")

    @property
    def state_class(self):
        return {"arrived": "st-arrived", "departed": "st-departed", "cancelled": "st-departed", "noshow": "st-noshow",
                "offschedule": "st-overdue"}.get(self.state[0], "st-expected")

    @property
    def dates_display(self):
        if self.date_from == self.date_to:
            return f"{self.date_from:%d.%m.%Y}"
        return f"{self.date_from:%d.%m}–{self.date_to:%d.%m.%Y}"


class DeliveryItem(models.Model):
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="items", verbose_name=_("поставка"))
    abp = models.CharField(_("АБП"), max_length=50, blank=True, help_text=_("Кто заказал: ПО, СГМ, СГЭ, ДКС, ДАПиИТ АП…"))
    supplier = models.CharField(_("подрядная организация"), max_length=300, blank=True)
    name = models.TextField(_("наименование оборудования / материала"))
    quantity = models.CharField(_("объём / кол-во"), max_length=100, blank=True)
    contract = models.CharField(_("№ договора / заказа"), max_length=300, blank=True)
    is_otp = models.BooleanField(_("ОТП — отечественный товаропроизводитель"), null=True, blank=True)
    is_final = models.BooleanField(_("финальная поставка"), null=True, blank=True)
    local_content_ok = models.BooleanField(_("местное содержание и СТ-KZ внесены"), null=True, blank=True)

    class Meta:
        verbose_name = _("позиция поставки")
        verbose_name_plural = _("позиции поставки")
        ordering = ["pk"]

    def __str__(self):
        return f"{self.name[:60]} — {self.quantity}"

    @property
    def abp_tone(self):
        """Постоянный цвет метки АБП: одна служба — всегда один цвет."""
        return sum(map(ord, self.abp.upper().strip())) % 6 if self.abp else ""
