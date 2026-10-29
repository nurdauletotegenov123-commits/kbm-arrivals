import os
import re

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import EntryBan, Organization, Point, Request, Trip, Visitor


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class TimeInput(forms.TimeInput):
    input_type = "time"

    def __init__(self, **kwargs):
        super().__init__(format="%H:%M", **kwargs)


class RequestForm(forms.ModelForm):
    class Meta:
        model = Request
        fields = ["purpose", "contact_name", "contact_phone", "comment"]
        widgets = {
            "comment": forms.Textarea(attrs={"rows": 2}),
            "contact_phone": forms.TextInput(attrs={"type": "tel", "placeholder": "+7 7__ ___ __ __"}),
            "purpose": forms.TextInput(attrs={"placeholder": _("Например: ремонт ГЗУ-12, проверка, совещание")}),
        }


class VisitorForm(forms.ModelForm):
    organization_name = forms.CharField(
        label=_("Организация"), max_length=200, widget=forms.TextInput(attrs={"list": "org-list", "autocomplete": "off"})
    )

    # После отметки «Прибыл» личные данные и дату прибытия менять нельзя.
    LOCKED_FIELDS = ("full_name", "doc_type", "doc_number", "organization_name", "position", "arrival_date", "arrival_time")

    class Meta:
        model = Visitor
        fields = [
            "full_name", "doc_type", "doc_number", "organization_name", "position",
            "arrival_date", "arrival_time", "departure_date", "one_day",
            "vehicle_make", "vehicle_plate", "needs_transport",
            "needs_camp", "needs_meals", "breakfast", "lunch", "dinner",
        ]
        widgets = {
            "arrival_date": DateInput(),
            "departure_date": DateInput(),
            "arrival_time": TimeInput(),
            "full_name": forms.TextInput(attrs={"placeholder": _("Фамилия Имя Отчество")}),
            "vehicle_plate": forms.TextInput(attrs={"placeholder": "123ABC12"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["departure_date"].required = False
        if self.instance.pk and self.instance.organization_id:
            self.fields["organization_name"].initial = self.instance.organization.name
        if self.instance.pk and self.instance.is_locked:
            for name in self.LOCKED_FIELDS:
                self.fields[name].disabled = True

    @property
    def is_locked(self):
        return bool(self.instance.pk and self.instance.is_locked)

    def clean(self):
        data = super().clean()
        doc_type, doc = data.get("doc_type"), (data.get("doc_number") or "").replace(" ", "")
        if doc and doc_type == Visitor.DocType.IIN and not re.fullmatch(r"\d{12}", doc):
            self.add_error("doc_number", _("ИИН должен состоять из 12 цифр."))
        data["doc_number"] = doc

        arrival = data.get("arrival_date")
        if data.get("one_day"):
            data["departure_date"] = arrival
            data["needs_camp"] = False
        elif arrival and not data.get("departure_date"):
            self.add_error("departure_date", _("Укажите дату выезда или отметьте «Однодневный визит»."))
        if arrival and not self.is_locked and "arrival_date" in self.changed_data and arrival < timezone.localdate():
            self.add_error("arrival_date", _("Дата прибытия не может быть в прошлом."))
        return data

    def _post_clean(self):
        # Значения, исправленные в clean(), должны попасть в модель.
        for name in ("departure_date", "needs_camp", "doc_number"):
            if name in self.cleaned_data:
                setattr(self.instance, name, self.cleaned_data[name])
        super()._post_clean()

    def save(self, commit=True):
        if not self.fields["organization_name"].disabled:
            name = " ".join(self.cleaned_data["organization_name"].split())
            org = Organization.objects.filter(name__iexact=name).first()
            self.instance.organization = org or Organization.objects.create(name=name)
        return super().save(commit)


class TripForm(forms.ModelForm):
    class Meta:
        model = Trip
        fields = ["time", "pickup_place", "vehicle_make", "vehicle_plate", "driver_name", "driver_phone", "comment"]
        widgets = {
            "time": TimeInput(),
            "vehicle_plate": forms.TextInput(attrs={"placeholder": "123ABC12"}),
            "vehicle_make": forms.TextInput(attrs={"placeholder": _("Например: Hyundai County")}),
            "pickup_place": forms.TextInput(attrs={"placeholder": _("Например: у главного входа в офис")}),
            "driver_phone": forms.TextInput(attrs={"type": "tel", "placeholder": "+7 7__ ___ __ __"}),
        }


CARGO_FILE_TYPES = (".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx", ".xls", ".xlsx")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Несколько файлов в одном поле (рецепт из документации Django)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data if d]
        return [single(data, initial)] if data else []


class CargoForm(forms.ModelForm):
    """Машина подрядчика с материалами. Заполняется в заявке, если стоит галочка."""

    has_cargo = forms.BooleanField(label=_("Завоз материалов или оборудования"), required=False)
    files = MultipleFileField(
        label=_("Документы"), required=False,
        help_text=_("Договор, накладная, сертификаты: PDF, фото, Word, Excel, до %(mb)s МБ каждый.")
        % {"mb": settings.CARGO_FILE_MAX_MB},
        widget=MultipleFileInput(attrs={"accept": ",".join(CARGO_FILE_TYPES)}),
    )
    REQUIRED = ("contract_number", "date", "vehicle_make", "vehicle_plate", "driver_name", "cargo")

    class Meta:
        model = Trip
        fields = ["contract_number", "date", "time", "vehicle_make", "vehicle_plate", "driver_name", "driver_phone",
                  "cargo"]
        labels = {"date": _("Дата завоза"), "time": _("Примерное время")}
        widgets = {
            "date": DateInput(),
            "time": TimeInput(),
            "cargo": forms.Textarea(attrs={"rows": 2, "placeholder": _("Например: трубы НКТ 73 мм — 120 шт., 2 т")}),
            "contract_number": forms.TextInput(attrs={"placeholder": _("Например: № 245-КБМ/2026 от 01.09.2026")}),
            "vehicle_make": forms.TextInput(attrs={"placeholder": _("Например: КАМАЗ 65117")}),
            "vehicle_plate": forms.TextInput(attrs={"placeholder": "123ABC12"}),
            "driver_phone": forms.TextInput(attrs={"type": "tel", "placeholder": "+7 7__ ___ __ __"}),
        }

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("prefix", "cargo")
        super().__init__(*args, **kwargs)
        self.fields["has_cargo"].initial = bool(self.instance.pk)
        for name in self.Meta.fields:
            self.fields[name].required = False
        self.existing_files = list(self.instance.files.all()) if self.instance.pk else []
        ids = {str(f.pk) for f in self.existing_files}
        raw = self.data.getlist(self.add_prefix("delete_file")) if hasattr(self.data, "getlist") else []
        self.delete_ids = [] if self.is_locked else [int(i) for i in raw if i in ids]
        if self.is_locked:  # машина уже на КПП
            for field in self.fields.values():
                field.disabled = True

    @property
    def is_locked(self):
        return bool(self.instance.pk and self.instance.is_marked)

    @property
    def wanted(self):
        return bool(self.cleaned_data.get("has_cargo"))

    def clean(self):
        data = super().clean()
        if not data.get("has_cargo"):
            return data
        for name in self.REQUIRED:
            if not data.get(name):
                self.add_error(name, _("Обязательное поле."))
        day = data.get("date")
        if day and not self.is_locked and "date" in self.changed_data and day < timezone.localdate():
            self.add_error("date", _("Дата завоза не может быть в прошлом."))
        files = data.get("files") or []
        for f in files:
            if os.path.splitext(f.name)[1].lower() not in CARGO_FILE_TYPES:
                self.add_error("files", _("«%(n)s»: можно PDF, JPG, PNG, Word или Excel.") % {"n": f.name})
            elif f.size > settings.CARGO_FILE_MAX_MB * 1024 * 1024:
                self.add_error("files", _("«%(n)s» больше %(mb)s МБ.") % {"n": f.name, "mb": settings.CARGO_FILE_MAX_MB})
        if len(self.existing_files) - len(self.delete_ids) + len(files) > settings.CARGO_FILES_MAX:
            self.add_error("files", _("Не больше %(n)s файлов к грузу.") % {"n": settings.CARGO_FILES_MAX})
        return data

    def _post_clean(self):
        if self.cleaned_data.get("has_cargo"):  # без галочки модель не проверяем
            super()._post_clean()


class RejectForm(forms.Form):
    reason = forms.CharField(
        label=_("Причина отказа"), max_length=1000,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": _("Её увидит заявитель")}),
    )


class BaseVisitorFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        active = 0
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.has_changed() and not form.instance.pk:
                continue
            if self.can_delete and self._should_delete_form(form):
                if form.instance.pk and form.instance.is_locked:
                    raise ValidationError(_("Нельзя отменить визит %(name)s — он уже отмечен на КПП.") % {"name": form.instance})
                continue
            active += 1
        if active == 0:
            raise ValidationError(_("В заявке должен быть хотя бы один человек."))


VisitorFormSet = inlineformset_factory(
    Request, Visitor, form=VisitorForm, formset=BaseVisitorFormSet, extra=0, can_delete=True, max_num=500
)
# Для новой заявки — сразу одна пустая карточка человека.
NewVisitorFormSet = inlineformset_factory(
    Request, Visitor, form=VisitorForm, formset=BaseVisitorFormSet, extra=1, can_delete=True, max_num=500
)


class ImportForm(forms.Form):
    file = forms.FileField(label=_("Файл Excel (.xlsx)"))

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > 5 * 1024 * 1024:
            raise ValidationError(_("Файл больше 5 МБ."))
        if not f.name.lower().endswith((".xlsx", ".xlsm")):
            raise ValidationError(_("Нужен файл в формате .xlsx"))
        return f


class PeriodForm(forms.Form):
    date_from = forms.DateField(label=_("С"), widget=DateInput())
    date_to = forms.DateField(label=_("По"), widget=DateInput())

    def clean(self):
        data = super().clean()
        a, b = data.get("date_from"), data.get("date_to")
        if a and b:
            if b < a:
                raise ValidationError(_("Дата «по» раньше даты «с»."))
            if (b - a).days > 366:
                raise ValidationError(_("Период не больше года."))
        return data


class BanForm(forms.ModelForm):
    class Meta:
        model = EntryBan
        fields = ["full_name", "doc_number", "vehicle_plate", "reason"]
        widgets = {
            "reason": forms.Textarea(attrs={"rows": 2, "placeholder": _("Например: нарушение ТБ 12.10.2026, акт №45")}),
            "vehicle_plate": forms.TextInput(attrs={"placeholder": "123ABC12"}),
            "full_name": forms.TextInput(attrs={"placeholder": _("ФИО человека или «Машина ТОО …»")}),
        }


class RideForm(forms.Form):
    """Заявка на машину. Кто едет — по одному в строке; на месторождение — «ФИО, ИИН»."""

    from_point = forms.ModelChoiceField(Point.objects.filter(is_active=True), label=_("Откуда"), required=False,
                                        empty_label=_("— выберите точку или впишите адрес —"))
    from_address = forms.CharField(label=_("Адрес, откуда забрать"), max_length=300, required=False)
    to_point = forms.ModelChoiceField(Point.objects.filter(is_active=True), label=_("Куда"), required=False,
                                      empty_label=_("— выберите точку или впишите адрес —"))
    to_address = forms.CharField(label=_("Адрес, куда отвезти"), max_length=300, required=False)
    date = forms.DateField(label=_("Дата"), widget=DateInput())
    time = forms.TimeField(label=_("Время подачи"), widget=TimeInput())
    passengers = forms.CharField(
        label=_("Кто едет"), required=False, widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("По одному человеку в строке. На месторождение — через запятую ФИО и ИИН: "
                    "«Иванов Иван Иванович, 900101300123». Только груз — оставьте пустым."),
    )
    cargo = forms.CharField(label=_("Груз (если есть)"), required=False,
                            widget=forms.Textarea(attrs={"rows": 2, "placeholder": _("Например: кабель ВВГ 3×2,5 — 2 бухты")}))
    with_return = forms.BooleanField(label=_("Нужна машина и обратно"), required=False)
    return_date = forms.DateField(label=_("Дата обратно"), required=False, widget=DateInput())
    return_time = forms.TimeField(label=_("Время обратно"), required=False, widget=TimeInput())
    contact_phone = forms.CharField(label=_("Контактный телефон"), max_length=50,
                                    widget=forms.TextInput(attrs={"type": "tel", "placeholder": "+7 7__ ___ __ __"}))
    comment = forms.CharField(label=_("Комментарий"), required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        data = super().clean()
        for side in ("from", "to"):
            if not data.get(f"{side}_point") and not (data.get(f"{side}_address") or "").strip():
                self.add_error(f"{side}_address", _("Выберите точку или впишите адрес."))
        if data.get("from_point") and data.get("from_point") == data.get("to_point"):
            self.add_error("to_point", _("Точки «Откуда» и «Куда» совпадают."))
        if data.get("date") and data["date"] < timezone.localdate():
            self.add_error("date", _("Дата не может быть в прошлом."))
        if data.get("with_return"):
            if not data.get("return_date"):
                data["return_date"] = data.get("date")
            if data.get("date") and data["return_date"] < data["date"]:
                self.add_error("return_date", _("Обратно — не раньше поездки туда."))
            if not data.get("return_time"):
                self.add_error("return_time", _("Укажите время обратно."))
        guarded = bool(data.get("to_point") and data["to_point"].is_guarded)
        data["people"] = self._people(data.get("passengers") or "", guarded)
        if not data["people"] and not (data.get("cargo") or "").strip():
            self.add_error("passengers", _("Укажите, кто едет, или опишите груз."))
        return data

    def _people(self, text, guarded):
        """[(ФИО, документ)]; на охраняемую территорию документ обязателен (его проверяет КПП)."""
        people = []
        for n, line in enumerate((l.strip() for l in text.splitlines()), start=1):
            if not line:
                continue
            name, _sep, doc = line.rpartition(",") if "," in line else (line, "", "")
            name, doc = " ".join(name.split()), doc.replace(" ", "")
            if guarded and not doc:
                self.add_error("passengers", _("Строка %(n)s: на месторождение укажите ИИН через запятую после ФИО.") % {"n": n})
            elif doc and doc.isdigit() and not re.fullmatch(r"\d{12}", doc):
                self.add_error("passengers", _("Строка %(n)s: ИИН должен состоять из 12 цифр.") % {"n": n})
            people.append((name[:200], doc[:50]))
        return people


class ReasonForm(forms.Form):
    reason = forms.CharField(label=_("Причина"), max_length=1000, widget=forms.Textarea(attrs={"rows": 2}))
