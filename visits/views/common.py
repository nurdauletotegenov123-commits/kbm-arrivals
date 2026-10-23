from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from accounts.models import User

R = User.Role
UNDO_KEY = "last_action"


def safe_next(request, fallback):
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, require_https=request.is_secure()):
        return nxt
    return reverse(fallback)


def redirect_back(request, fallback, anchor=None):
    url = safe_next(request, fallback)
    if anchor:
        url = url.split("#")[0] + f"#{anchor}"
    return redirect(url)


def remember_action(request, visitor, text, undo_url):
    """Плашка «✓ Отмечено … [Отменить]» после одного нажатия."""
    request.session[UNDO_KEY] = {"pk": visitor.pk, "text": text, "undo_url": undo_url}


def pop_action(request):
    return request.session.pop(UNDO_KEY, None)
