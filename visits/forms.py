import re

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Organization, Request, Trip, Visitor


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


class CargoForm(forms.ModelForm):
    """Машина подрядчика с материалами. Заполняется в заявке, если стоит галочка."""

    has_cargo = forms.BooleanField(label=_("Завоз материалов или оборудования"), required=False)
    REQUIRED = ("date", "vehicle_make", "vehicle_plate", "driver_name", "cargo")

    class Meta:
        model = Trip
        fields = ["date", "time", "vehicle_make", "vehicle_plate", "driver_name", "driver_phone", "cargo"]
        labels = {"date": _("Дата завоза"), "time": _("Примерное время")}
        widgets = {
            "date": DateInput(),
            "time": TimeInput(),
            "cargo": forms.Textarea(attrs={"rows": 2, "placeholder": _("Например: трубы НКТ 73 мм — 120 шт., 2 т")}),
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
