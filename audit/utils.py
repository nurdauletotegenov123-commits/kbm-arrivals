from django.conf import settings

from .models import AuditLog


def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR") if settings.BEHIND_PROXY else None
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_action(request, action, obj=None, changes=None, user=None, object_repr=None):
    """Записать действие в журнал. `request` может быть None (например, в командах)."""
    if user is None and request is not None and request.user.is_authenticated:
        user = request.user
    return AuditLog.objects.create(
        user=user,
        username=user.username if user else "",
        action=action,
        object_type=obj._meta.model_name if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        object_repr=(object_repr or (str(obj) if obj is not None else ""))[:300],
        changes=changes or {},
        ip_address=client_ip(request),
    )


def diff_fields(before, after):
    """Сравнить два словаря значений и вернуть {поле: [было, стало]}."""
    return {k: [before.get(k), after.get(k)] for k in after if before.get(k) != after.get(k)}
