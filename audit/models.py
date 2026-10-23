from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class AuditLog(models.Model):
    class Action(models.TextChoices):
        LOGIN = "login", _("Вход в систему")
        LOGOUT = "logout", _("Выход из системы")
        LOGIN_FAILED = "login_failed", _("Неудачный вход")
        REQUEST_CREATE = "request_create", _("Создание заявки")
        REQUEST_UPDATE = "request_update", _("Изменение заявки")
        REQUEST_CANCEL = "request_cancel", _("Отмена заявки")
        VISITOR_ADD = "visitor_add", _("Добавлен человек")
        VISITOR_UPDATE = "visitor_update", _("Изменены данные человека")
        VISITOR_CANCEL = "visitor_cancel", _("Отменён визит человека")
        ARRIVE = "arrive", _("Отметка «Прибыл»")
        DEPART = "depart", _("Отметка «Убыл»")
        UNDO_MARK = "undo_mark", _("Отмена отметки КПП")
        CAMP_CHECK_IN = "camp_check_in", _("Заселение в кэмп")
        CAMP_CHECK_OUT = "camp_check_out", _("Выселение из кэмпа")
        CAMP_ROOM = "camp_room", _("Изменение комнаты")
        TRIP_CREATE = "trip_create", _("Назначен транспорт")
        TRIP_UPDATE = "trip_update", _("Изменён рейс")
        TRIP_DELETE = "trip_delete", _("Удалён рейс")
        TRIP_ENTER = "trip_enter", _("Машина въехала")
        TRIP_EXIT = "trip_exit", _("Машина выехала")
        TRIP_UNDO = "trip_undo", _("Отмена отметки машины")
        IMPORT = "import", _("Импорт из Excel")
        EXPORT = "export", _("Выгрузка отчёта")
        USER_CREATE = "user_create", _("Создание пользователя")
        USER_UPDATE = "user_update", _("Изменение пользователя")
        USER_PASSWORD = "user_password", _("Смена пароля")

    created_at = models.DateTimeField(_("время"), auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name=_("пользователь"), null=True, blank=True, on_delete=models.SET_NULL
    )
    username = models.CharField(_("логин"), max_length=150, blank=True)
    action = models.CharField(_("действие"), max_length=30, choices=Action.choices, db_index=True)
    object_type = models.CharField(_("тип объекта"), max_length=50, blank=True)
    object_id = models.CharField(_("ID объекта"), max_length=50, blank=True)
    object_repr = models.CharField(_("объект"), max_length=300, blank=True)
    changes = models.JSONField(_("изменения"), default=dict, blank=True)
    ip_address = models.GenericIPAddressField(_("IP-адрес"), null=True, blank=True)

    class Meta:
        verbose_name = _("запись журнала")
        verbose_name_plural = _("журнал действий")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["object_type", "object_id"])]

    def __str__(self):
        return f"{self.created_at:%d.%m.%Y %H:%M} {self.username} {self.get_action_display()}"
