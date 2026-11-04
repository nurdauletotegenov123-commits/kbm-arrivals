from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from visits.forms import DateInput

from .models import Delivery, DeliveryItem


class DeliveryForm(forms.ModelForm):
    class Meta:
        model = Delivery
        fields = ["date_from", "date_to", "vehicle", "truck_plate", "trailer_plate", "driver_name", "driver_doc",
                  "contact_name", "contact_phone", "departure_point", "work_type", "note"]
        widgets = {
            "date_from": DateInput(), "date_to": DateInput(),
            "vehicle": forms.TextInput(attrs={"placeholder": _("Например: КАМАЗ 5410, VOLVO FH")}),
            "truck_plate": forms.TextInput(attrs={"placeholder": "585AFU12", "autocapitalize": "characters"}),
            "trailer_plate": forms.TextInput(attrs={"placeholder": "97AMH17", "autocapitalize": "characters"}),
            "contact_phone": forms.TextInput(attrs={"type": "tel", "placeholder": "+7 7__ ___ __ __"}),
        }
        labels = {"date_from": _("Въезд с"), "date_to": _("Въезд по")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["truck_plate"].required = True
        self.fields["date_to"].required = False

    def clean(self):
        data = super().clean()
        d1, d2 = data.get("date_from"), data.get("date_to")
        if d1 and not d2:
            data["date_to"] = d1
        elif d1 and d2 and d2 < d1:
            self.add_error("date_to", _("«По» раньше, чем «с»."))
        if d1 and not self.instance.pk and d1 < timezone.localdate() - timezone.timedelta(days=7):
            self.add_error("date_from", _("Дата въезда слишком давно в прошлом."))
        return data

    def _post_clean(self):
        if "date_to" in self.cleaned_data:
            self.instance.date_to = self.cleaned_data["date_to"]
        super()._post_clean()


class ItemForm(forms.ModelForm):
    YESNO = [("", "—"), ("true", _("да")), ("false", _("нет"))]
    is_otp = forms.NullBooleanField(label=_("ОТП"), required=False, widget=forms.Select(choices=YESNO))
    is_final = forms.NullBooleanField(label=_("Финальная"), required=False, widget=forms.Select(choices=YESNO))
    local_content_ok = forms.NullBooleanField(label=_("СТ-KZ"), required=False, widget=forms.Select(choices=YESNO))

    class Meta:
        model = DeliveryItem
        fields = ["abp", "supplier", "name", "quantity", "contract", "is_otp", "is_final", "local_content_ok"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": _("Например: Штанга насосная ШН-22-8000 Д супер с ТВЧ")}),
            "abp": forms.TextInput(attrs={"placeholder": "ПО", "list": "abp-list"}),
            "quantity": forms.TextInput(attrs={"placeholder": "720 шт"}),
        }


ItemFormSet = inlineformset_factory(Delivery, DeliveryItem, form=ItemForm, extra=0, min_num=1, validate_min=True,
                                    can_delete=True, max_num=100)


class UploadForm(forms.Form):
    file = forms.FileField(label=_("Файл плана поставок (.xlsx)"))

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith((".xlsx", ".xlsm")):
            raise forms.ValidationError(_("Нужен файл Excel .xlsx. Если у вас PDF — попросите исходный Excel."))
        if f.size > 10 * 1024 * 1024:
            raise forms.ValidationError(_("Файл больше 10 МБ."))
        return f
