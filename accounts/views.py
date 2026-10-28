from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from audit.models import AuditLog
from audit.utils import client_ip, diff_fields, log_action

from . import telegram
from .decorators import role_required
from .forms import USER_FIELDS, LoginForm, PasswordForm, SignupForm, UserCreateForm, UserForm
from .models import User

R = User.Role
HOME_BY_ROLE = {R.APPLICANT: "request_list", R.CHIEF: "monitor", R.SECURITY: "kpp", R.CAMP: "camp",
                R.DISPATCHER: "dispatch"}


def _too_many(request, action, limit, minutes):
    """Простая защита сайта, открытого в интернет: не больше `limit` событий с одного IP за `minutes` минут.
    Считаем по журналу действий, отдельный кэш не нужен."""
    ip = client_ip(request)
    since = timezone.now() - timedelta(minutes=minutes)
    return bool(ip) and AuditLog.objects.filter(action=action, ip_address=ip, created_at__gte=since).count() >= limit


class LoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True
    form_class = LoginForm

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "signup_enabled": settings.SIGNUP_ENABLED,
                "ad_enabled": settings.AD_ENABLED}

    def post(self, request, *args, **kwargs):
        if _too_many(request, AuditLog.Action.LOGIN_FAILED, settings.LOGIN_FAILS_LIMIT, 15):
            messages.error(request, _("Слишком много неудачных попыток входа. Подождите 15 минут."))
            return redirect("login")
        return super().post(request, *args, **kwargs)


def signup(request):
    if not settings.SIGNUP_ENABLED:
        raise Http404
    if request.user.is_authenticated:
        return redirect("home")
    form = SignupForm(request.POST or None)
    if request.method == "POST":
        if _too_many(request, AuditLog.Action.USER_SIGNUP, settings.SIGNUPS_PER_HOUR, 60):
            messages.error(request, _("С этого подключения уже зарегистрировано много аккаунтов. Попробуйте позже."))
        elif form.is_valid():
            user = form.save()
            log_action(request, AuditLog.Action.USER_SIGNUP, user, {"организация": user.organization}, user=user)
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, _("Вы зарегистрированы. Логин для входа — ваш номер телефона."))
            return redirect("request_list")
    return render(request, "registration/signup.html", {"form": form})


@login_required
def home(request):
    if request.user.is_admin_role:
        return redirect("dashboard")
    return redirect(HOME_BY_ROLE.get(request.user.role, "request_list"))


def _snapshot(user):
    return {f: str(getattr(user, f)) if getattr(user, f) is not None else None for f in USER_FIELDS}


@role_required(R.ADMIN)
def user_list(request):
    qs = User.objects.select_related("checkpoint")
    q = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    if request.GET.get("self") == "1":
        qs = qs.filter(self_registered=True)
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(full_name__icontains=q) | Q(email__icontains=q)
                       | Q(organization__icontains=q))
    if role:
        qs = qs.filter(role=role)
    page = Paginator(qs.order_by("role", "full_name", "username"), 50).get_page(request.GET.get("page"))
    return render(request, "accounts/user_list.html", {"page": page, "q": q, "role": role, "roles": R.choices,
                                                       "self_only": request.GET.get("self") == "1"})


@role_required(R.ADMIN)
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if form.is_valid():
        user = form.save()
        log_action(request, AuditLog.Action.USER_CREATE, user, _snapshot(user))
        messages.success(request, _("Пользователь %(u)s создан.") % {"u": user.username})
        return redirect("user_list")
    return render(request, "accounts/user_form.html", {"form": form, "is_new": True})


@role_required(R.ADMIN)
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    before = _snapshot(user)
    form = UserForm(request.POST or None, instance=user)
    if form.is_valid():
        if user == request.user and (not form.cleaned_data["is_active"] or form.cleaned_data["role"] != R.ADMIN) \
                and not user.is_superuser:
            messages.error(request, _("Нельзя лишить себя роли администратора или отключить свой аккаунт."))
        else:
            user = form.save()
            changes = diff_fields(before, _snapshot(user))
            if changes:
                log_action(request, AuditLog.Action.USER_UPDATE, user, changes)
            messages.success(request, _("Сохранено."))
            return redirect("user_list")
    return render(request, "accounts/user_form.html", {"form": form, "obj": user, "is_new": False})


@role_required(R.ADMIN)
def user_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    form = PasswordForm(user, request.POST or None)
    if form.is_valid():
        form.save()
        log_action(request, AuditLog.Action.USER_PASSWORD, user)
        messages.success(request, _("Пароль для %(u)s изменён.") % {"u": user.username})
        return redirect("user_list")
    return render(request, "accounts/user_password.html", {"form": form, "obj": user})


@login_required
def own_password(request):
    form = PasswordForm(request.user, request.POST or None)
    if form.is_valid():
        form.save()
        from django.contrib.auth import update_session_auth_hash

        update_session_auth_hash(request, request.user)
        log_action(request, AuditLog.Action.USER_PASSWORD, request.user)
        messages.success(request, _("Пароль изменён."))
        return redirect("home")
    return render(request, "accounts/user_password.html", {
        "form": form, "obj": request.user, "own": True, "telegram_enabled": settings.TELEGRAM_ENABLED,
    })


@require_POST
@login_required
def telegram_connect(request):
    """Одноразовая ссылка на бота: человек жмёт «Start», бот запоминает его chat_id."""
    if not settings.TELEGRAM_ENABLED:
        raise Http404
    return redirect(telegram.link_url(request.user))


@require_POST
@login_required
def telegram_disconnect(request):
    user = request.user
    if user.telegram_chat_id:
        telegram.send_message(user.telegram_chat_id, _("Уведомления отключены на сайте."))
        user.telegram_chat_id = ""
        user.save(update_fields=["telegram_chat_id"])
        log_action(request, AuditLog.Action.USER_UPDATE, user, {"telegram": "отключён"})
    messages.info(request, _("Telegram отключён."))
    return redirect("own_password")
