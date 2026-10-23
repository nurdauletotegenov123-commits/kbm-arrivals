from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from audit.models import AuditLog
from audit.utils import diff_fields, log_action

from .decorators import role_required
from .forms import USER_FIELDS, PasswordForm, UserCreateForm, UserForm
from .models import User

R = User.Role
HOME_BY_ROLE = {R.APPLICANT: "request_list", R.SECURITY: "kpp", R.CAMP: "camp", R.DISPATCHER: "dispatch"}


class LoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True


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
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(full_name__icontains=q) | Q(email__icontains=q))
    if role:
        qs = qs.filter(role=role)
    page = Paginator(qs.order_by("role", "full_name", "username"), 50).get_page(request.GET.get("page"))
    return render(request, "accounts/user_list.html", {"page": page, "q": q, "role": role, "roles": R.choices})


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
    return render(request, "accounts/user_password.html", {"form": form, "obj": request.user, "own": True})
