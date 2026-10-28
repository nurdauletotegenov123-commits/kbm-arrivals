from django.contrib.auth.models import AbstractUser, UserManager as BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _


class Checkpoint(models.Model):
    """КПП месторождения. Гость КПП не выбирает: все КПП видят всех ожидаемых,
    а при отметке фиксируется, через какой КПП человек прошёл."""

    name = models.CharField(_("название"), max_length=100, unique=True)
    is_active = models.BooleanField(_("активен"), default=True)

    class Meta:
        verbose_name = _("КПП")
        verbose_name_plural = _("КПП")
        ordering = ["name"]

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", User.Role.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        APPLICANT = "applicant", _("Заявитель")
        CHIEF = "chief", _("Начальник штаба безопасности")  # согласует заявки
        SECURITY = "security", _("Охрана (КПП)")
        CAMP = "camp", _("Кэмп и столовая")  # один человек ведёт кэмп и докладывает на кухню
        DISPATCHER = "dispatcher", _("Диспетчер транспорта")
        ADMIN = "admin", _("Администратор")

    full_name = models.CharField(_("ФИО"), max_length=200, blank=True)
    phone = models.CharField(_("телефон"), max_length=50, blank=True)
    organization = models.CharField(_("организация"), max_length=200, blank=True)
    self_registered = models.BooleanField(
        _("зарегистрировался сам"), default=False, editable=False,
        help_text=_("Аккаунт создан через страницу регистрации с телефона."),
    )
    role = models.CharField(_("роль"), max_length=20, choices=Role.choices, default=Role.APPLICANT)
    checkpoint = models.ForeignKey(
        Checkpoint,
        verbose_name=_("КПП"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text=_("Для охраны: на каком КПП работает этот аккаунт."),
    )
    notify_by_email = models.BooleanField(
        _("получать уведомления на почту"),
        default=True,
        help_text=_("Для охраны, кэмпа/столовой и диспетчера: письма о новых и изменённых заявках."),
    )

    # Telegram: chat_id появляется, когда человек нажал «Подключить Telegram» и «Start» в боте.
    telegram_chat_id = models.CharField(_("Telegram chat id"), max_length=32, blank=True, editable=False)
    telegram_token = models.CharField(max_length=32, blank=True, editable=False)
    telegram_token_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = UserManager()

    class Meta:
        verbose_name = _("пользователь")
        verbose_name_plural = _("пользователи")
        ordering = ["full_name", "username"]

    def __str__(self):
        return self.full_name or self.username

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN or self.is_superuser

    def has_role(self, *roles):
        """Администратор имеет доступ ко всем разделам."""
        return self.is_admin_role or self.role in roles

    @property
    def display_name(self):
        return self.full_name or self.username

    @property
    def can_see_documents(self):
        """ИИН/паспорт видят только охрана, начальник штаба и администратор
        (заявитель — в своих заявках, это проверяется отдельно)."""
        return self.has_role(self.Role.SECURITY, self.Role.CHIEF)
