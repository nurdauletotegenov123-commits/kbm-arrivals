from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditLog
from .utils import client_ip, log_action


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log_action(request, AuditLog.Action.LOGIN, user=user, object_repr=str(user))


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log_action(request, AuditLog.Action.LOGOUT, user=user, object_repr=str(user))


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    AuditLog.objects.create(
        username=(credentials.get("username") or "")[:150],
        action=AuditLog.Action.LOGIN_FAILED,
        ip_address=client_ip(request),
    )
