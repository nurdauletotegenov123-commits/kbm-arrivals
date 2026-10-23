from django import forms
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _

from .models import User

USER_FIELDS = ["username", "full_name", "role", "checkpoint", "phone", "email", "notify_by_email", "is_active"]


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
