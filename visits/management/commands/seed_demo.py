"""Тестовые аккаунты и данные, чтобы сразу посмотреть все экраны.

    python manage.py seed_demo           # создать (если ещё нет)
    python manage.py seed_demo --reset   # удалить все заявки и создать заново
"""
import random
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Checkpoint, User
from audit.models import AuditLog
from deliveries.models import Delivery
from visits import rides, services
from visits.models import EntryBan, Organization, Point, Request, RidePassenger, RideRequest, Trip, Visitor

R = User.Role
PASSWORD = "demo1234"
USERS = [
    ("admin", "Администратор системы", R.ADMIN, None),
    ("zayavitel", "Сагинтаев Ерлан Маратович", R.APPLICANT, None),
    ("zayavitel2", "Кенжебаева Айгуль Сериковна", R.APPLICANT, None),
    ("nachalnik", "Начальник штаба безопасности", R.CHIEF, None),
    ("ohrana", "Пост охраны КПП-1", R.SECURITY, "КПП-1 (главный въезд)"),
    ("ohrana2", "Пост охраны КПП-2", R.SECURITY, "КПП-2 (промзона)"),
    ("kemp", "Администратор кэмпа и столовой", R.CAMP, None),
    ("dispetcher", "Диспетчер транспорта", R.DISPATCHER, None),
    ("snab", "Отдел снабжения (ДМТС)", R.SUPPLY, None),
]
NAMES = [
    "Абенов Нурлан Серикович", "Жумабаев Данияр Ерланович", "Касымов Арман Болатович", "Утегенов Бауыржан Каиржанович",
    "Мусин Тимур Аскарович", "Оспанов Ержан Муратович", "Сейткалиев Азамат Нурланович", "Тулегенов Руслан Жанатович",
    "Исмаилов Марат Есенович", "Бекмуханов Канат Талгатович", "Ахметова Динара Болатовна", "Нургалиев Асхат Ринатович",
    "Шарипов Серик Кайратович", "Досмагамбетов Ильяс Маратович", "Ермеков Айбек Нуржанович", "Кадыров Олжас Бахытович",
    "Сарсенов Медет Галымович", "Иванов Сергей Петрович", "Сулейменов Ербол Жаксылыкович", "Жаксылыков Мейрам Абаевич",
    "Тажибаев Алмас Кайратович", "Бисенов Ерлан Сабитович", "Калиев Нуржан Амангельдиевич", "Мукашев Дамир Ерланович",
    "Султанова Жанар Маратовна", "Есимов Бекзат Нурланович",
]
POSITIONS = ["Оператор ДНГ", "Слесарь КИПиА", "Электромонтёр", "Мастер бригады", "Инженер-технолог", "Помощник бурильщика",
             "Водитель", "Сварщик", "Геолог"]


def fake_iin(rng):
    return f"{rng.randint(70, 99):02d}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}3{rng.randint(0, 9999):04d}{rng.randint(0, 9)}"


class Command(BaseCommand):
    help = "Создать тестовые аккаунты (пароль demo1234) и тестовые заявки"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="удалить все заявки и журнал перед созданием")

    @transaction.atomic
    def handle(self, *args, reset=False, **opts):
        if reset:
            Visitor.objects.all().delete()
            Request.objects.all().delete()
            Trip.objects.all().delete()
            EntryBan.objects.all().delete()
            RideRequest.objects.all().delete()
            Delivery.objects.all().delete()
            AuditLog.objects.all().delete()
        elif Request.objects.exists():
            self.stdout.write(self.style.WARNING("Заявки уже есть — данные не созданы. Используйте --reset."))
            self._users()
            return
        users = self._users()
        self._data(users)
        self.stdout.write(self.style.SUCCESS("Готово. Логины: " + ", ".join(u for u, *_ in USERS) + f"; пароль: {PASSWORD}"))

    def _users(self):
        users = {}
        for username, full_name, role, cp_name in USERS:
            cp = Checkpoint.objects.get_or_create(name=cp_name)[0] if cp_name else None
            user, created = User.objects.get_or_create(username=username, defaults={
                "full_name": full_name, "role": role, "checkpoint": cp, "phone": "+7 701 000 00 00",
                "is_staff": role == R.ADMIN, "is_superuser": role == R.ADMIN, "notify_by_email": False,
            })
            if created:
                user.set_password(PASSWORD)
                user.save()
            users[username] = user
        return users

    def _data(self, users):
        rng = random.Random(42)
        today = timezone.localdate()
        d = lambda n: today + timedelta(days=n)  # noqa: E731
        own = Organization.objects.get_or_create(name="АО «Каражанбасмунай»", defaults={"kind": Organization.Kind.OWN})[0]
        orgs = {name: Organization.objects.get_or_create(name=name)[0] for name in
                ["ТОО «Бурсервис-КБМ»", "ТОО «Мангистау Энерго Монтаж»", "ТОО «КазМунайТранс-Сервис»", "ТОО «Каспий Нефть Сервис»"]}
        guest = Organization.objects.get_or_create(name="ТОО «Аудит-Консалт»", defaults={"kind": Organization.Kind.OTHER})[0]
        names = iter(NAMES)
        a1, a2, sec, sec2, camp = users["zayavitel"], users["zayavitel2"], users["ohrana"], users["ohrana2"], users["kemp"]
        disp, chief = users["dispetcher"], users["nachalnik"]

        def req(author, purpose, comment="", approval=Request.Approval.APPROVED):
            decided = approval != Request.Approval.PENDING
            return Request.objects.create(author=author, purpose=purpose, contact_name=author.full_name,
                                          contact_phone="+7 701 234 56 78" if author == a1 else "+7 702 345 67 89",
                                          comment=comment, approval=approval,
                                          approved_by=chief if decided else None,
                                          approved_at=timezone.now() - timedelta(days=1) if decided else None)

        def person(r, org, arr, dep, *, at=None, camp_=False, meals="", plate="", make="", doc=None, name=None,
                   transport=False):
            v = Visitor.objects.create(
                request=r, full_name=name or next(names), organization=org, position=rng.choice(POSITIONS),
                doc_type=doc[0] if doc else Visitor.DocType.IIN, doc_number=doc[1] if doc else fake_iin(rng),
                arrival_date=arr, departure_date=dep, arrival_time=at, one_day=arr == dep,
                vehicle_plate=plate, vehicle_make=make, needs_transport=transport, needs_camp=camp_ and arr != dep,
                needs_meals=bool(meals), breakfast="з" in meals, lunch="о" in meals, dinner="у" in meals,
            )
            AuditLog.objects.create(user=r.author, username=r.author.username, action=AuditLog.Action.VISITOR_ADD,
                                    object_type="visitor", object_id=str(v.pk), object_repr=str(v))
            return v

        def arrive(v, days_ago=0, hh=8, by=sec):
            services.mark_arrived(v.pk, by)
            Visitor.objects.filter(pk=v.pk).update(arrived_at=_at(today - timedelta(days=days_ago), hh))

        def trip(direction, day, people, plate, make, driver, hh=None, place=""):
            t, _ = services.assign_trip(Trip(direction=direction, date=day, vehicle_plate=plate, vehicle_make=make,
                                             pickup_place=place,
                                             driver_name=driver, driver_phone="+7 705 111 22 33",
                                             time=time(hh, 0) if hh else None), [v.pk for v in people], disp)
            return t

        def check_in(v, building, room, days_ago=0):
            services.camp_check_in(v.pk, camp, building, room)
            Visitor.objects.filter(pk=v.pk).update(checked_in_at=_at(today - timedelta(days=days_ago), 10))

        # 1. Гость на один день сегодня, с обедом, на своей машине
        r = req(a1, "Аудит системы промышленной безопасности", "Встретить на КПП-1, проводить в АБК")
        person(r, guest, d(0), d(0), at=time(10, 0), meals="о", plate="777AUD02", make="Toyota Camry")

        # 2. Бригада подрядчика на неделю: кэмп + питание + транспорт. Первую машину диспетчер уже
        #    назначил (двое прибыли, водитель уехал), остальные четверо ещё без машины.
        r = req(a1, "Капитальный ремонт скважин, участок №3", "Бригада КРС")
        brigade = [person(r, orgs["ТОО «Бурсервис-КБМ»"], d(0), d(7), at=time(8, 30), camp_=True, meals="зоу",
                          transport=True) for i in range(6)]
        t = trip(Trip.Direction.IN, d(0), brigade[:2], "045KRS12", "Toyota HiAce", "Жумагалиев Серик", hh=8,
                 place="г. Актау, у главного входа в офис «Каражанбасмунай»")
        services.trip_enter(t.pk, sec)
        arrive(brigade[0], hh=8)
        arrive(brigade[1], hh=8)
        services.trip_exit(t.pk, sec)
        check_in(brigade[0], "Корпус 2", "214")

        # 3. Не прибыл вчера (должен был заселиться)
        r = req(a2, "Наладка оборудования ГЗУ")
        person(r, orgs["ТОО «Мангистау Энерго Монтаж»"], d(-1), d(3), at=time(9, 0), camp_=True, meals="зоу")

        # 4. Живёт 3-й день, выезжает сегодня; машину на вывоз диспетчер ещё не назначил
        r = req(a2, "Монтаж ЛЭП 6 кВ")
        for _ in range(2):
            v = person(r, orgs["ТОО «Мангистау Энерго Монтаж»"], d(-3), d(0), camp_=True, meals="зоу", transport=True)
            arrive(v, days_ago=3, hh=9, by=sec2)
            check_in(v, "Корпус 1", f"10{_ + 3}", days_ago=3)

        # 5. Проживает, выезд через 4 дня
        r = req(a1, "Сопровождение бурения, супервайзер")
        v = person(r, orgs["ТОО «Каспий Нефть Сервис»"], d(-5), d(4), camp_=True, meals="зоу",
                   name="Петренко Алексей Викторович", doc=(Visitor.DocType.PASSPORT, "FA1234567"))
        arrive(v, days_ago=5, hh=14)
        check_in(v, "Корпус 3", "301", days_ago=5)

        # 6. Просрочен выезд: должен был уехать вчера
        r = req(a2, "Доставка ГСМ")
        v = person(r, orgs["ТОО «КазМунайТранс-Сервис»"], d(-2), d(-1), meals="оу", plate="318KMT12", make="КамАЗ 6520")
        arrive(v, days_ago=2, hh=11)

        # 7. Завтра — группа из 4 человек с кэмпом, машина на привоз уже назначена
        r = req(a1, "Обучение по охране труда")
        group = [person(r, own, d(1), d(3), at=time(9, 0), camp_=True, meals="зоу", transport=True) for _ in range(4)]
        trip(Trip.Direction.IN, d(1), group, "118KBM12", "Hyundai County", "Есенов Арман", hh=9,
                 place="г. Актау, у главного входа в офис «Каражанбасмунай»")

        # 8. Через 3 дня — один человек с кэмпом, без питания
        r = req(a2, "Проверка учёта нефти")
        person(r, orgs["ТОО «Каспий Нефть Сервис»"], d(3), d(5), camp_=True)

        # 9. Однодневный визит завтра, обед
        r = req(a2, "Совещание по плану ГТМ")
        person(r, own, d(1), d(1), at=time(11, 0), meals="о", plate="901KBM12", make="Toyota Land Cruiser")

        # 10. Уехал вчера
        r = req(a1, "Замер дебита")
        v = person(r, own, d(-1), d(-1), meals="о")
        arrive(v, days_ago=1, hh=9)
        services.mark_departed(v.pk, sec)
        Visitor.objects.filter(pk=v.pk).update(departed_at=_at(d(-1), 17))

        # 11. Отменённая заявка
        r = req(a1, "Поставка труб (перенесено)")
        person(r, orgs["ТОО «КазМунайТранс-Сервис»"], d(2), d(2), plate="220KMT12")
        services.cancel_request(r, a1)

        # 12. На согласовании: бригада подрядчика с завозом материалов
        r = req(a1, "Замена выкидной линии скв. 1245", "Нужен допуск на кустовую площадку",
                approval=Request.Approval.PENDING)
        crew = [person(r, orgs["ТОО «Бурсервис-КБМ»"], d(1), d(4), at=time(8, 0), camp_=True, meals="зоу")
                for _ in range(3)]
        # одному из бригады раньше запретили въезд — начальник увидит это при согласовании
        EntryBan.objects.create(full_name=crew[2].full_name, doc_number=crew[2].doc_number, created_by=chief,
                                reason="Нарушение ТБ на кусте 1245, акт №45 от 12.09.2026")
        Trip.objects.create(direction=Trip.Direction.CARGO, request=r, date=d(1), time=time(10, 0),
                            vehicle_make="КАМАЗ 65117", vehicle_plate="512BRS12", driver_name="Ахметов Болат",
                            driver_phone="+7 707 555 44 33", cargo="Трубы НКТ 73 мм — 120 шт., задвижки — 6 шт.",
                            contract_number="№ 245-КБМ/2026 от 01.09.2026",
                            created_by=a1)

        # 13. На согласовании: гость на один день
        r = req(a2, "Аудит пожарной безопасности", approval=Request.Approval.PENDING)
        person(r, guest, d(2), d(2), at=time(10, 0), meals="о", plate="555AUD02", make="Kia K5")

        # 14. Отклонена начальником штаба
        r = req(a2, "Экскурсия для студентов", approval=Request.Approval.REJECTED)
        Request.objects.filter(pk=r.pk).update(reject_reason="Нет приказа о практике. Приложите письмо от вуза.")
        person(r, guest, d(3), d(3))

        # 15. Согласована, сегодня въезжает машина с грузом
        r = req(a1, "Поставка химреагентов на УПН")
        person(r, orgs["ТОО «КазМунайТранс-Сервис»"], d(0), d(0), at=time(11, 0), name="Сериков Дастан Ерболович")
        Trip.objects.create(direction=Trip.Direction.CARGO, request=r, date=d(0), time=time(11, 0),
                            vehicle_make="MAN TGS", vehicle_plate="707KMT12", driver_name="Сериков Дастан",
                            driver_phone="+7 701 999 88 77", cargo="Деэмульгатор, 20 бочек по 200 л",
                            contract_number="№ 112-ХР/2026", created_by=a1)

        # 16. Заявки на машину: по городу (ждёт и с машиной) и на месторождение (на согласовании)
        office, bmts, field = (Point.objects.get(name=n) for n in ("Офис Актау", "БМТС (склад)", "Месторождение"))

        def ride(author, frm, to, day, hh, people, cargo="", approval=RideRequest.Approval.NOT_NEEDED):
            r = RideRequest.objects.create(author=author, from_point=frm, to_point=to, date=day, time=time(hh, 0),
                                           cargo=cargo, contact_phone="+7 701 234 56 78", approval=approval)
            for n, doc in people:
                RidePassenger.objects.create(ride=r, full_name=n, doc_number=doc)
            return r

        ride(a1, bmts, office, d(0), 15, [("Сагинтаев Ерлан Маратович", "")], cargo="Картриджи для принтеров, 1 коробка")
        r = ride(a2, office, bmts, d(1), 9, [("Кенжебаева Айгуль Сериковна", ""), ("Мукашев Дамир Ерланович", "")])
        t, _ = rides.assign_rides(Trip(date=d(1), vehicle_make="Toyota Camry", vehicle_plate="321KBM12",
                                       driver_name="Оразов Нурлан", driver_phone="+7 705 222 33 44"), [r.pk], disp)
        ride(a1, bmts, field, d(1), 8, [("Калиев Нуржан Амангельдиевич", fake_iin(rng))],
             cargo="Задвижки Ду100 — 4 шт.", approval=RideRequest.Approval.PENDING)

        # 17. Поставки: план снабжения на сегодня (как их ежедневный Excel), одна машина уже въехала
        from deliveries import sample, services as delivery_services
        from deliveries.importer import apply_plan, parse_plan

        apply_plan(parse_plan(sample.build(d(0)))[0], users["snab"])
        delivery_services.enter(Delivery.objects.get(truck_plate="808XXA17").pk, sec)

        for r in Request.objects.all():
            AuditLog.objects.create(user=r.author, username=r.author.username, action=AuditLog.Action.REQUEST_CREATE,
                                    object_type="request", object_id=str(r.pk), object_repr=str(r))


def _at(day, hour):
    return timezone.make_aware(datetime.combine(day, time(hour, rng_minute())))


def rng_minute(_r=random.Random(7)):
    return _r.randint(0, 59)
