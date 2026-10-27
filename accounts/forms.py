import re

from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _

from .models import User

USER_FIELDS = ["username", "full_name", "role", "checkpoint", "organization", "phone", "email", "notify_by_email", "is_active"]


def normalize_phone(value):
    """+7 (701) 123-45-67, 87011234567, 7011234567 → 77011234567. None, если это не казахстанский номер."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    return digits if len(digits) == 11 and digits[0] == "7" else None


def looks_like_phone(value):
    return bool(re.fullmatch(r"[\d\s()+-]{10,}", (value or "").strip()))


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = USER_FIELDS
        labels = {"username": _("Логин"), "is_active": _("Активен (может входить)")}
        help_texts = {"username": _("Латиница, цифры и символы @.+-_"), "is_active": ""}


    def save(self, commit=True):
        user = super().save(commit=False)
        # Роль «Администратор» = доступ и к служебной админке Django (справочник КПП и т.п.)
        user.is_staff = user.is_superuser = user.role == User.Role.ADMIN
        if commit:
            user.save()
        return user


class UserCreateForm(UserForm):
    password1 = forms.CharField(label=_("Пароль"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label=_("Пароль ещё раз"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", _("Пароли не совпадают."))
        elif p1:
            try:
                validate_password(p1, self.instance)
            except forms.ValidationError as e:
                self.add_error("password1", e)
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class PasswordForm(SetPasswordForm):
    pass


class LoginForm(AuthenticationForm):
    """Вход по логину или по номеру телефона (так входят те, кто зарегистрировался сам)."""

    def clean_username(self):
        value = self.cleaned_data.get("username", "")
        if looks_like_phone(value):
            return normalize_phone(value) or value
        return value


class SignupForm(forms.ModelForm):
    """Самостоятельная регистрация заявителя (подрядчики, гости) с телефона.
    Аккаунт сразу активен, но любая заявка проходит согласование начальника штаба."""

    phone = forms.CharField(label=_("Мобильный телефон"), max_length=30,
                            help_text=_("Он же будет логином для входа."),
                            widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel", "placeholder": "+7 7__ ___ __ __"}))
    password1 = forms.CharField(label=_("Пароль"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label=_("Пароль ещё раз"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    # Ловушка для ботов: поле скрыто, человек его не заполнит.
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))

    class Meta:
        model = User
        fields = ["full_name", "organization", "phone", "email"]
        labels = {"email": _("E-mail (необязательно)")}
        help_texts = {"email": _("Сюда придёт письмо, когда заявку согласуют и назначат машину.")}
        widgets = {
            "full_name": forms.TextInput(attrs={"autocomplete": "name", "placeholder": _("Фамилия Имя Отчество")}),
            "organization": forms.TextInput(attrs={"autocomplete": "organization", "placeholder": _("Например: ТОО «Бурсервис»")}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["full_name"].required = True
        self.fields["organization"].required = True

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        if not phone:
            raise forms.ValidationError(_("Введите номер в формате +7 7XX XXX XX XX."))
        if User.objects.filter(username=phone).exists():
            raise forms.ValidationError(_("Этот номер уже зарегистрирован. Войдите или обратитесь к администратору, если забыли пароль."))
        return phone

    def clean(self):
        data = super().clean()
        if data.get("website"):
            raise forms.ValidationError(_("Не удалось зарегистрироваться."))
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", _("Пароли не совпадают."))
        elif p1:
            try:
                validate_password(p1, User(username=data.get("phone") or "", full_name=data.get("full_name") or ""))
            except forms.ValidationError as e:
                self.add_error("password1", e)
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        phone = self.cleaned_data["phone"]
        user.username = phone
        user.phone = f"+{phone[0]} {phone[1:4]} {phone[4:7]} {phone[7:9]} {phone[9:]}"
        user.role = User.Role.APPLICANT
        user.self_registered = True
        user.notify_by_email = bool(user.email)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user
