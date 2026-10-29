import os
import uuid
from datetime import time as datetime_time, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .text import normalize_plate, normalize_search, search_terms


class Organization(models.Model):
    class Kind(models.TextChoices):
        OWN = "own", _("АО «Каражанбасмунай»")
        CONTRACTOR = "contractor", _("Подрядчик")
        OTHER = "other", _("Другое (гость)")

    name = models.CharField(_("название"), max_length=200, unique=True)
    kind = models.CharField(_("тип"), max_length=20, choices=Kind.choices, default=Kind.CONTRACTOR)

    class Meta:
        verbose_name = _("организация")
        verbose_name_plural = _("организации")
        ordering = ["name"]

    def __str__(self):
        return self.name


class Request(models.Model):
    """Заявка. Может содержать одного человека или целую бригаду.
    Пока начальник штаба безопасности её не согласовал, никто кроме него её не видит."""

    class Approval(models.TextChoices):
        PENDING = "pending", _("На согласовании")
        APPROVED = "approved", _("Согласована")
        REJECTED = "rejected", _("Отклонена")

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("автор"), on_delete=models.PROTECT, related_name="requests"
    )
    purpose = models.CharField(_("цель визита"), max_length=300)
    contact_name = models.CharField(_("ответственный (ФИО)"), max_length=200)
    contact_phone = models.CharField(_("телефон ответственного"), max_length=50)
    comment = models.TextField(_("комментарий"), blank=True)
    is_cancelled = models.BooleanField(_("отменена"), default=False)
    approval = models.CharField(
        _("согласование"), max_length=20, choices=Approval.choices, default=Approval.PENDING, db_index=True
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("решение принял"), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    approved_at = models.DateTimeField(_("время решения"), null=True, blank=True)
    reject_reason = models.TextField(_("причина отказа"), blank=True)
    created_at = models.DateTimeField(_("создана"), auto_now_add=True)
    updated_at = models.DateTimeField(_("изменена"), auto_now=True)

    class Meta:
        verbose_name = _("заявка")
        verbose_name_plural = _("заявки")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Заявка №{self.pk}"

    @property
    def number(self):
        return f"{self.pk:05d}" if self.pk else ""

    def status_counts(self):
        """{статус: количество} по людям заявки — для списка заявок."""
        counts = {}
        for v in self.visitors.all():
            counts[v.status] = counts.get(v.status, 0) + 1
        return counts

    def status_badges(self):
        labels = dict(Visitor.Status.choices)
        order = [Visitor.Status.EXPECTED, Visitor.Status.ARRIVED, Visitor.Status.DEPARTED, Visitor.Status.CANCELLED,
                 Visitor.Status.REJECTED]
        counts = self.status_counts()
        no_show = sum(1 for v in self.visitors.all() if v.is_no_show)
        badges = []
        if no_show:
            badges.append({"key": "noshow", "label": _("Не прибыл"), "count": no_show})
        for key in order:
            count = counts.get(key, 0) - (no_show if key == Visitor.Status.EXPECTED else 0)
            if count > 0:
                badges.append({"key": key, "label": labels[key], "count": count})
        return badges

    def trips(self):
        """Машины, назначенные людям этой заявки: [{"trip": Trip, "people": [...]}], по времени."""
        found = {}
        for v in self.visitors.all():
            if v.is_cancelled:
                continue
            for trip in (v.arrival_trip, v.departure_trip):
                if trip:  # груз сюда не попадает: у него нет пассажиров
                    found.setdefault(trip.pk, {"trip": trip, "people": []})["people"].append(v)
        return sorted(found.values(), key=lambda t: (t["trip"].date, t["trip"].time or datetime_time.min, t["trip"].pk))

    def transport_state(self):
        """Для списка заявок: None — транспорт не нужен; "waiting" — кому-то ещё не назначена машина
        на привоз; "arrival" — привоз назначен, вывоз ещё нет; "assigned" — назначено всё."""
        people = [v for v in self.visitors.all() if v.needs_transport and not v.is_cancelled]
        if not people:
            return None
        if any(v.waits_arrival_trip for v in people):
            return "waiting"
        return "arrival" if any(v.waits_departure_trip for v in people) else "assigned"

    def date_range(self):
        visitors = [v for v in self.visitors.all() if v.status not in Visitor.GONE] or list(self.visitors.all())
        if not visitors:
            return None, None
        return min(v.arrival_date for v in visitors), max(v.departure_date for v in visitors)

    @property
    def can_be_edited(self):
        return not self.is_cancelled

    @property
    def is_pending(self):
        return self.approval == self.Approval.PENDING

    @property
    def is_approved(self):
        return self.approval == self.Approval.APPROVED

    @property
    def is_rejected(self):
        return self.approval == self.Approval.REJECTED

    @property
    def cargo_trip(self):
        """Машина с материалами подрядчика (одна на заявку)."""
        if not self.pk:
            return None
        return next((t for t in self.cargo_trips.all()), None)


class Trip(models.Model):
    """Рейс диспетчера: машина, которая привозит людей на месторождение или вывозит их.
    Водитель в тот же день уезжает, поэтому машина отмечается на КПП отдельно от пассажиров."""

    class Direction(models.TextChoices):
        IN = "in", _("Привоз")
        OUT = "out", _("Вывоз")
        CARGO = "cargo", _("Завоз груза")  # машина подрядчика с материалами, указывает заявитель
        RIDE = "ride", _("Поездка")  # по заявкам на машину: офис, БМТС, месторождение

    direction = models.CharField(_("направление"), max_length=10, choices=Direction.choices)
    request = models.ForeignKey(
        Request, verbose_name=_("заявка"), null=True, blank=True, on_delete=models.CASCADE, related_name="cargo_trips",
        help_text=_("Только для завоза груза."),
    )
    cargo = models.TextField(_("что везут"), blank=True, help_text=_("Материалы, оборудование, количество."))
    contract_number = models.CharField(_("номер договора"), max_length=100, blank=True)
    date = models.DateField(_("дата"), db_index=True)
    time = models.TimeField(_("время подачи"), null=True, blank=True)
    pickup_place = models.CharField(
        _("место подачи"), max_length=300, blank=True,
        help_text=_("Где машина забирает людей, например: у главного входа в офис «Каражанбасмунай»."),
    )
    vehicle_make = models.CharField(_("марка авто"), max_length=100)
    vehicle_plate = models.CharField(_("госномер"), max_length=20, db_index=True)
    driver_name = models.CharField(_("водитель (ФИО)"), max_length=200)
    driver_phone = models.CharField(_("телефон водителя"), max_length=50, blank=True)
    comment = models.CharField(_("комментарий"), max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("диспетчер"), null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(_("создан"), auto_now_add=True)

    entered_at = models.DateTimeField(_("въехала"), null=True, blank=True)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("отметил въезд"), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    entered_checkpoint = models.ForeignKey(
        "accounts.Checkpoint", verbose_name=_("КПП въезда"), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    exited_at = models.DateTimeField(_("выехала"), null=True, blank=True)
    exited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("отметил выезд"), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    exited_checkpoint = models.ForeignKey(
        "accounts.Checkpoint", verbose_name=_("КПП выезда"), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        verbose_name = _("рейс")
        verbose_name_plural = _("рейсы (транспорт)")
        ordering = ["date", "time", "pk"]

    def __str__(self):
        return f"{self.get_direction_display()} {self.date:%d.%m} {self.vehicle_plate}"

    def save(self, *args, **kwargs):
        self.vehicle_plate = normalize_plate(self.vehicle_plate)
        super().save(*args, **kwargs)

    @property
    def is_in(self):
        return self.direction == self.Direction.IN

    @property
    def is_cargo(self):
        return self.direction == self.Direction.CARGO

    @property
    def is_ride(self):
        return self.direction == self.Direction.RIDE

    @property
    def is_marked(self):
        return bool(self.entered_at or self.exited_at)

    @property
    def state_class(self):
        if self.exited_at:
            return "st-departed"
        if self.entered_at:
            return "st-arrived"
        return "st-expected"

    @property
    def has_expected_passengers(self):
        return self.passengers().filter(status=Visitor.Status.EXPECTED).exists()

    def passengers(self):
        """Пассажиры рейса без отменённых."""
        if self.is_cargo or self.is_ride:
            return Visitor.objects.none()
        related = self.arrivals if self.is_in else self.departures
        return related.active().select_related("organization", "request")


class VisitorQuerySet(models.QuerySet):
    def active(self):
        return self.exclude(status__in=Visitor.GONE).filter(request__is_cancelled=False)

    def approved(self):
        """Заявка согласована начальником штаба — человека видит диспетчер."""
        return self.active().filter(request__approval=Request.Approval.APPROVED)

    def ready(self):
        """Видны охране и кэмпу: заявка согласована, и если нужен транспорт — диспетчер уже назначил
        машину на привоз. Кто уже прошёл КПП, виден в любом случае (даже если согласование потом отозвали)."""
        S = Visitor.Status
        waiting = models.Q(request__approval=Request.Approval.APPROVED) & (
            models.Q(needs_transport=False) | models.Q(arrival_trip__isnull=False)
        )
        return self.active().filter(waiting | models.Q(status__in=[S.ARRIVED, S.DEPARTED]))

    def with_camp(self):
        return self.ready().filter(needs_camp=True)

    def with_meals(self):
        return self.ready().filter(needs_meals=True)

    def search(self, query):
        cond = models.Q()
        for term in search_terms(query):
            cond |= models.Q(search_index__contains=term)
        plate = normalize_plate(query)
        if plate:  # госномер машины диспетчера
            cond |= models.Q(arrival_trip__vehicle_plate__contains=plate) | models.Q(departure_trip__vehicle_plate__contains=plate)
        return self.filter(cond) if cond else self

    def on_site_on(self, day):
        """Люди, которые по плану находятся на месторождении в этот день."""
        return self.filter(arrival_date__lte=day, departure_date__gte=day)

    def no_shows(self, today=None):
        today = today or timezone.localdate()
        since = today - timedelta(days=settings.NO_SHOW_DAYS)
        return self.active().filter(
            status=Visitor.Status.EXPECTED, arrival_date__lt=today, arrival_date__gte=since
        )


class Visitor(models.Model):
    class DocType(models.TextChoices):
        IIN = "iin", _("ИИН")
        ID_CARD = "id_card", _("Удостоверение личности")
        PASSPORT = "passport", _("Паспорт")

    class Status(models.TextChoices):
        EXPECTED = "expected", _("Ожидается")
        ARRIVED = "arrived", _("Прибыл")
        DEPARTED = "departed", _("Убыл")
        CANCELLED = "cancelled", _("Отменён")
        REJECTED = "rejected", _("Не согласован")  # снят начальником штаба при согласовании

    class CampStatus(models.TextChoices):
        WAITING = "waiting", _("Ожидает заселения")
        CHECKED_IN = "checked_in", _("Заселён")
        CHECKED_OUT = "checked_out", _("Выселен")

    # Отменённые заявителем и снятые начальником штаба: их нигде не учитывают.
    GONE = (Status.CANCELLED, Status.REJECTED)

    request = models.ForeignKey(Request, verbose_name=_("заявка"), on_delete=models.CASCADE, related_name="visitors")

    full_name = models.CharField(_("ФИО"), max_length=200)
    doc_type = models.CharField(_("тип документа"), max_length=20, choices=DocType.choices, default=DocType.IIN)
    doc_number = models.CharField(_("номер документа / ИИН"), max_length=50)
    organization = models.ForeignKey(
        Organization, verbose_name=_("организация"), on_delete=models.PROTECT, related_name="visitors"
    )
    position = models.CharField(_("должность"), max_length=200, blank=True)

    arrival_date = models.DateField(_("дата прибытия"), db_index=True)
    arrival_time = models.TimeField(_("примерное время прибытия"), null=True, blank=True)
    departure_date = models.DateField(_("дата выезда"), db_index=True)
    one_day = models.BooleanField(_("однодневный визит"), default=False)

    vehicle_make = models.CharField(_("марка авто"), max_length=100, blank=True)
    vehicle_plate = models.CharField(_("госномер"), max_length=20, blank=True, db_index=True)
    needs_transport = models.BooleanField(
        _("нужен транспорт"), default=False,
        help_text=_("Машину на привоз и вывоз назначит диспетчер."),
    )
    arrival_trip = models.ForeignKey(
        Trip, verbose_name=_("машина на привоз"), null=True, blank=True, on_delete=models.SET_NULL, related_name="arrivals"
    )
    departure_trip = models.ForeignKey(
        Trip, verbose_name=_("машина на вывоз"), null=True, blank=True, on_delete=models.SET_NULL, related_name="departures"
    )

    needs_camp = models.BooleanField(_("проживание в кэмпе"), default=False)
    needs_meals = models.BooleanField(_("питание"), default=False)
    breakfast = models.BooleanField(_("завтрак"), default=False)
    lunch = models.BooleanField(_("обед"), default=False)
    dinner = models.BooleanField(_("ужин"), default=False)

    status = models.CharField(_("статус"), max_length=20, choices=Status.choices, default=Status.EXPECTED, db_index=True)
    arrived_at = models.DateTimeField(_("время прибытия"), null=True, blank=True)
    arrived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("отметил прибытие"), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    arrived_checkpoint = models.ForeignKey(
        "accounts.Checkpoint", verbose_name=_("КПП прибытия"), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    departed_at = models.DateTimeField(_("время убытия"), null=True, blank=True)
    departed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("отметил убытие"), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    departed_checkpoint = models.ForeignKey(
        "accounts.Checkpoint", verbose_name=_("КПП убытия"), null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    camp_status = models.CharField(
        _("статус кэмпа"), max_length=20, choices=CampStatus.choices, default=CampStatus.WAITING
    )
    camp_building = models.CharField(_("корпус"), max_length=50, blank=True)
    camp_room = models.CharField(_("комната"), max_length=50, blank=True)
    checked_in_at = models.DateTimeField(_("время заселения"), null=True, blank=True)
    checked_out_at = models.DateTimeField(_("время выселения"), null=True, blank=True)

    # Заявитель перенёс даты уже согласованной заявки: что было до переноса и когда.
    # Несколько дней это подсвечивается у КПП, кэмпа и диспетчера.
    prev_arrival_date = models.DateField(_("прежняя дата прибытия"), null=True, blank=True, editable=False)
    prev_departure_date = models.DateField(_("прежняя дата выезда"), null=True, blank=True, editable=False)
    dates_changed_at = models.DateTimeField(_("даты изменены"), null=True, blank=True, editable=False)

    # Строка для поиска без учёта регистра (SQLite не умеет icontains для кириллицы).
    search_index = models.CharField(max_length=500, blank=True, editable=False, db_index=True)

    objects = VisitorQuerySet.as_manager()

    class Meta:
        verbose_name = _("человек")
        verbose_name_plural = _("люди в заявках")
        ordering = ["arrival_date", "arrival_time", "full_name"]

    def __str__(self):
        return self.full_name

    def save(self, *args, **kwargs):
        self.vehicle_plate = normalize_plate(self.vehicle_plate)
        self._drop_stale_trips()
        self.doc_number = (self.doc_number or "").strip().replace(" ", "")
        org = self.organization.name if self.organization_id else ""
        self.search_index = normalize_search(
            " ".join([self.full_name, self.doc_number, self.vehicle_plate, org])
        )[:500]
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"search_index"}
        super().save(*args, **kwargs)

    def _drop_stale_trips(self):
        """Если заявитель перенёс дату или снял галочку «Нужен транспорт», назначенная машина
        больше не подходит — человек снова попадает к диспетчеру в «Без машины»."""
        if not self.needs_transport:
            self.arrival_trip = self.departure_trip = None
            return
        if self.arrival_trip_id and self.status == self.Status.EXPECTED and self.arrival_trip.date != self.arrival_date:
            self.arrival_trip = None
        if (self.departure_trip_id and self.status != self.Status.DEPARTED
                and self.departure_trip.date != self.departure_date):
            self.departure_trip = None

    def clean(self):
        errors = {}
        if self.needs_transport:
            self.vehicle_make = self.vehicle_plate = ""
        if self.one_day:
            self.departure_date = self.arrival_date
            self.needs_camp = False
        if self.arrival_date and self.departure_date and self.departure_date < self.arrival_date:
            errors["departure_date"] = _("Дата выезда не может быть раньше даты прибытия.")
        if self.needs_meals and not (self.breakfast or self.lunch or self.dinner):
            errors["needs_meals"] = _("Отметьте хотя бы один приём пищи.")
        if not self.needs_meals:
            self.breakfast = self.lunch = self.dinner = False
        if errors:
            raise ValidationError(errors)

    # --- вычисляемые признаки для экранов ---

    @property
    def is_cancelled(self):
        return self.status in self.GONE or self.request.is_cancelled

    @property
    def is_no_show(self):
        """Не прибыл в указанный день (дата прошла, отметки нет)."""
        return (
            self.status == self.Status.EXPECTED
            and not self.request.is_cancelled
            and self.arrival_date < timezone.localdate()
        )

    @property
    def leaves_today(self):
        return self.status == self.Status.ARRIVED and self.departure_date == timezone.localdate()

    @property
    def is_overdue(self):
        """Должен был уехать, но отметки «Убыл» нет."""
        return self.status == self.Status.ARRIVED and self.departure_date < timezone.localdate()

    @property
    def is_early(self):
        return self.status == self.Status.EXPECTED and self.arrival_date > timezone.localdate()

    @property
    def waits_arrival_trip(self):
        """Нужна машина на привоз, а диспетчер ещё не назначил."""
        return self.needs_transport and not self.arrival_trip_id and self.status == self.Status.EXPECTED

    @property
    def waits_departure_trip(self):
        return (self.needs_transport and not self.departure_trip_id
                and self.status in (self.Status.EXPECTED, self.Status.ARRIVED))

    @property
    def state_class(self):
        """CSS-класс карточки: подсветка «не прибыл», «просрочен выезд» и т.п."""
        if self.is_cancelled:
            return "st-cancelled"
        if self.is_no_show:
            return "st-noshow"
        if self.is_overdue:
            return "st-overdue"
        return f"st-{self.status}"

    @property
    def date_change(self):
        """Недавний перенос дат: {"arrival": (было, стало), "departure": (было, стало)} или None."""
        if not self.dates_changed_at:
            return None
        if timezone.now() - self.dates_changed_at > timedelta(days=settings.DATE_CHANGE_DAYS):
            return None
        change = {}
        if self.prev_arrival_date and self.prev_arrival_date != self.arrival_date:
            change["arrival"] = (self.prev_arrival_date, self.arrival_date)
        if self.prev_departure_date and self.prev_departure_date != self.departure_date:
            change["departure"] = (self.prev_departure_date, self.departure_date)
        return change or None

    def record_date_change(self, old):
        """Запомнить перенос дат относительно `old` (копии до правки). True, если даты изменились."""
        changed = False
        if old.arrival_date != self.arrival_date:
            self.prev_arrival_date, changed = old.arrival_date, True
        if old.departure_date != self.departure_date:
            self.prev_departure_date, changed = old.departure_date, True
        if changed:
            self.dates_changed_at = timezone.now()
        return changed

    @property
    def nights(self):
        return (self.departure_date - self.arrival_date).days

    @property
    def is_locked(self):
        """После прибытия заявитель может менять только дату выезда."""
        return self.status in (self.Status.ARRIVED, self.Status.DEPARTED)

    @property
    def meals_display(self):
        meals = [str(label) for flag, label in (
            (self.breakfast, _("завтрак")), (self.lunch, _("обед")), (self.dinner, _("ужин"))
        ) if flag]
        return ", ".join(meals)

    @property
    def room_display(self):
        return " / ".join(p for p in (self.camp_building, self.camp_room) if p)

    def masked_doc(self):
        if len(self.doc_number) <= 4:
            return "••••"
        return "••••••" + self.doc_number[-4:]

    def eats_on(self, day):
        """Числится ли на питании в этот день (план, с учётом досрочного убытия)."""
        if not self.needs_meals or self.is_cancelled:
            return False
        if not (self.arrival_date <= day <= self.departure_date):
            return False
        if self.status == self.Status.DEPARTED and self.departed_at:
            return timezone.localtime(self.departed_at).date() >= day
        return True


def _cargo_upload_to(instance, filename):
    """Случайное имя на диске: по ссылке не угадать. Исходное имя хранится в `name`."""
    ext = os.path.splitext(filename)[1].lower()
    return f"cargo/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{ext}"


class CargoFile(models.Model):
    """Документ к грузу: договор, накладная, сертификат. Видят автор заявки, начальник штаба,
    охрана (после согласования) и администратор."""

    trip = models.ForeignKey(Trip, verbose_name=_("груз"), on_delete=models.CASCADE, related_name="files")
    file = models.FileField(_("файл"), upload_to=_cargo_upload_to)
    name = models.CharField(_("имя файла"), max_length=200)
    size = models.PositiveIntegerField(_("размер"), default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("загрузил"), null=True, on_delete=models.SET_NULL, related_name="+"
    )
    uploaded_at = models.DateTimeField(_("загружен"), auto_now_add=True)

    class Meta:
        verbose_name = _("документ на груз")
        verbose_name_plural = _("документы на груз")
        ordering = ["uploaded_at", "pk"]

    def __str__(self):
        return self.name

    @property
    def is_image(self):
        return os.path.splitext(self.name)[1].lower() in (".jpg", ".jpeg", ".png")


@receiver(post_delete, sender=CargoFile)
def _remove_cargo_file(sender, instance, **kwargs):
    instance.file.delete(save=False)


class EntryBan(models.Model):
    """Запрет въезда: ведёт начальник штаба безопасности. Совпадение ищется по документу/ИИН
    и по госномеру. Заявитель запрет и причину не видит."""

    full_name = models.CharField(_("ФИО / описание"), max_length=200)
    doc_number = models.CharField(_("ИИН / номер документа"), max_length=50, blank=True, db_index=True)
    vehicle_plate = models.CharField(_("госномер"), max_length=20, blank=True, db_index=True)
    reason = models.TextField(_("причина"))
    is_active = models.BooleanField(_("действует"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("кто добавил"), null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(_("добавлен"), auto_now_add=True)
    lifted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("кто снял"), null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    lifted_at = models.DateTimeField(_("снят"), null=True, blank=True)

    class Meta:
        verbose_name = _("запрет въезда")
        verbose_name_plural = _("запреты въезда")
        ordering = ["-is_active", "-created_at"]

    def __str__(self):
        return self.full_name

    def save(self, *args, **kwargs):
        self.doc_number = (self.doc_number or "").replace(" ", "").upper()
        self.vehicle_plate = normalize_plate(self.vehicle_plate)
        super().save(*args, **kwargs)

    def clean(self):
        if not (self.doc_number or "").strip() and not (self.vehicle_plate or "").strip():
            raise ValidationError(_("Укажите ИИН/документ или госномер — по ним КПП и согласование найдут совпадение."))


class Point(models.Model):
    """Точка маршрута для заявок на машину: офис в Актау, склад БМТС, месторождение."""

    name = models.CharField(_("название"), max_length=100, unique=True)
    address = models.CharField(_("адрес / место подачи"), max_length=300, blank=True,
                               help_text=_("Где машина забирает людей, например: у главного входа."))
    is_guarded = models.BooleanField(
        _("охраняемая территория"), default=False,
        help_text=_("Въезд через КПП: поездка сюда согласуется начальником штаба безопасности."),
    )
    is_active = models.BooleanField(_("активна"), default=True)
    order = models.PositiveSmallIntegerField(_("порядок"), default=0)

    class Meta:
        verbose_name = _("точка маршрута")
        verbose_name_plural = _("точки маршрута")
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class RideQuerySet(models.QuerySet):
    def for_dispatcher(self):
        """Ждут или уже получили машину: согласование не нужно или пройдено."""
        A = RideRequest.Approval
        return self.filter(approval__in=[A.NOT_NEEDED, A.APPROVED],
                           status__in=[RideRequest.Status.NEW, RideRequest.Status.ASSIGNED])


class RideRequest(models.Model):
    """Заявка на машину: любой сотрудник просит отвезти людей или груз из точки в точку.
    Если едут на охраняемую территорию (месторождение) — сначала согласует начальник штаба,
    после назначения машины её видит КПП."""

    class Approval(models.TextChoices):
        NOT_NEEDED = "not_needed", _("Не требуется")
        PENDING = "pending", _("На согласовании")
        APPROVED = "approved", _("Согласована")
        REJECTED = "rejected", _("Отклонена")

    class Status(models.TextChoices):
        NEW = "new", _("Ждёт машину")
        ASSIGNED = "assigned", _("Машина назначена")
        DONE = "done", _("Выполнена")
        REFUSED = "refused", _("Отказано диспетчером")
        CANCELLED = "cancelled", _("Отменена")

    author = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("автор"), on_delete=models.PROTECT,
                               related_name="rides")
    from_point = models.ForeignKey(Point, verbose_name=_("откуда"), null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    from_address = models.CharField(_("откуда (адрес)"), max_length=300, blank=True)
    to_point = models.ForeignKey(Point, verbose_name=_("куда"), null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")
    to_address = models.CharField(_("куда (адрес)"), max_length=300, blank=True)
    date = models.DateField(_("дата"), db_index=True)
    time = models.TimeField(_("время подачи"), null=True, blank=True)
    cargo = models.TextField(_("груз"), blank=True, help_text=_("Что везём, если есть: материалы, оборудование."))
    contact_phone = models.CharField(_("контактный телефон"), max_length=50)
    comment = models.TextField(_("комментарий"), blank=True)

    approval = models.CharField(_("согласование"), max_length=20, choices=Approval.choices,
                                default=Approval.NOT_NEEDED, db_index=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_("решение принял"), null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(_("время решения"), null=True, blank=True)
    status = models.CharField(_("статус"), max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    reason = models.TextField(_("причина отказа"), blank=True)
    trip = models.ForeignKey(Trip, verbose_name=_("машина"), null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="rides")
    return_of = models.OneToOneField("self", verbose_name=_("обратная поездка к"), null=True, blank=True,
                                     on_delete=models.CASCADE, related_name="return_leg")
    created_at = models.DateTimeField(_("создана"), auto_now_add=True)
    updated_at = models.DateTimeField(_("изменена"), auto_now=True)

    objects = RideQuerySet.as_manager()

    class Meta:
        verbose_name = _("заявка на машину")
        verbose_name_plural = _("заявки на машину")
        ordering = ["-date", "-time", "-pk"]

    def __str__(self):
        return f"Поездка №{self.number}"

    @property
    def number(self):
        return f"{self.pk:05d}" if self.pk else ""

    @property
    def from_display(self):
        return str(self.from_point) if self.from_point_id else self.from_address

    @property
    def to_display(self):
        return str(self.to_point) if self.to_point_id else self.to_address

    @property
    def route(self):
        return f"{self.from_display} → {self.to_display}"

    @property
    def pickup_place(self):
        """Где подать машину: адрес точки или введённый адрес."""
        if self.from_point_id:
            return ", ".join(p for p in (self.from_point.name, self.from_point.address) if p)
        return self.from_address

    @property
    def to_guarded(self):
        return bool(self.to_point_id and self.to_point.is_guarded)

    @property
    def touches_guarded(self):
        """Машина въезжает на охраняемую территорию (везёт туда или забирает оттуда) — её видит КПП."""
        return self.to_guarded or bool(self.from_point_id and self.from_point.is_guarded)

    @property
    def is_open(self):
        return self.status in (self.Status.NEW, self.Status.ASSIGNED) and self.approval != self.Approval.REJECTED

    @property
    def state_label(self):
        if self.approval in (self.Approval.PENDING, self.Approval.REJECTED):
            return self.get_approval_display()
        return self.get_status_display()

    @property
    def state_key(self):
        if self.approval in (self.Approval.PENDING, self.Approval.REJECTED):
            return self.approval
        return {"new": "overdue", "assigned": "trip-in", "done": "departed", "refused": "rejected",
                "cancelled": "cancelled"}[self.status]


class RidePassenger(models.Model):
    ride = models.ForeignKey(RideRequest, verbose_name=_("поездка"), on_delete=models.CASCADE,
                             related_name="passengers")
    full_name = models.CharField(_("ФИО"), max_length=200)
    doc_number = models.CharField(_("ИИН / документ"), max_length=50, blank=True)
    vehicle_plate = ""  # для сверки с запретом въезда (у пассажира машины нет)

    class Meta:
        verbose_name = _("пассажир")
        verbose_name_plural = _("пассажиры")
        ordering = ["pk"]

    def __str__(self):
        return self.full_name
