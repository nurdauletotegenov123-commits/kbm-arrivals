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
    class PermitRole(models.TextChoices):
        """Роль в модуле «Наряд-допуски» (из системы ЭНД hse.kbm.kz)."""
        WORK_PRODUCER = "WORK_PRODUCER", _("Производитель работ")
        ISSUER = "ISSUER", _("Выдающий наряд")
        ADMITTING = "ADMITTING", _("Допускающий")
        RESPONSIBLE = "RESPONSIBLE", _("Ответственный руководитель работ")
        COORDINATOR = "COORDINATOR", _("Согласующий")
        AUDITOR = "AUDITOR", _("Аудитор")
        ADMIN = "ADMIN", _("Администратор нарядов")

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
    from_ad = models.BooleanField(
        _("учётная запись домена"), default=False, editable=False,
        help_text=_("Входит под доменным логином и паролем; ФИО, почта и роль берутся из AD."),
    )
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

    # --- Наряд-допуски ---
    permit_role = models.CharField(_("роль в нарядах"), max_length=20, choices=PermitRole.choices,
                                   default=PermitRole.WORK_PRODUCER)
    iin = models.CharField(_("ИИН"), max_length=12, null=True, blank=True, unique=True,
                           help_text=_("Нужен для подписи нарядов ЭЦП: сверяется с ИИН в сертификате."))
    bin = models.CharField(_("БИН организации"), max_length=12, blank=True, default="000000000000",
                           help_text=_("Если сотрудник подписывает ЭЦП юридического лица."))
    tabel_number = models.CharField(_("табельный номер"), max_length=20, null=True, blank=True, unique=True)
    position = models.CharField(_("должность"), max_length=255, blank=True)
    surname = models.CharField(_("отчество"), max_length=150, blank=True)
    company_name = models.CharField(_("компания"), max_length=50, blank=True, default="АО Каражанбасмунай")
    department = models.ForeignKey("permits.Department", verbose_name=_("подразделение"), null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

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
    def is_admin(self):
        """Для модуля нарядов: администратор портала или администратор нарядов."""
        return self.is_admin_role or self.permit_role == self.PermitRole.ADMIN

    @property
    def is_auditor(self):
        return self.permit_role == self.PermitRole.AUDITOR

    def get_full_name(self):
        """Наряды печатают ФИО: из AD приходит целиком в full_name."""
        return self.full_name or " ".join(p for p in (self.last_name, self.first_name, self.surname) if p) \
            or self.username

    @property
    def display_name(self):
        return self.full_name or self.username

    @property
    def can_see_documents(self):
        """ИИН/паспорт видят только охрана, начальник штаба и администратор
        (заявитель — в своих заявках, это проверяется отдельно)."""
        return self.has_role(self.Role.SECURITY, self.Role.CHIEF)
