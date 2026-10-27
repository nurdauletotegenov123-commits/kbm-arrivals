from datetime import timedelta
from io import BytesIO

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from accounts.models import Checkpoint, User
from audit.models import AuditLog

from .excel import parse_people
from .notifications import notify_request
from .models import Organization, Request, Trip, Visitor

R = User.Role


def make_user(username, role, **kw):
    u = User.objects.create(username=username, role=role, full_name=username, **kw)
    u.set_password("pass12345")
    u.save()
    return u


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.cp = Checkpoint.objects.create(name="КПП-1")
        self.applicant = make_user("app", R.APPLICANT)
        self.other = make_user("app2", R.APPLICANT)
        self.security = make_user("sec", R.SECURITY, checkpoint=self.cp)
        self.camp = make_user("camp", R.CAMP)
        self.dispatcher = make_user("disp", R.DISPATCHER)
        self.org = Organization.objects.create(name="ТОО Тест")

    def visitor(self, **kw):
        req = kw.pop("request", None) or Request.objects.create(
            author=self.applicant, purpose="Работы", contact_name="Отв", contact_phone="1",
            approval=Request.Approval.APPROVED)
        data = dict(request=req, full_name="Иванов Иван", doc_number="900101300123", organization=self.org,
                    arrival_date=self.today, departure_date=self.today + timedelta(days=2))
        data.update(kw)
        return Visitor.objects.create(**data)

    def login(self, user):
        self.client.force_login(user)


class RequestFormTests(Base):
    def post_data(self, **person):
        p = {"full_name": "Петров Пётр", "doc_type": "iin", "doc_number": "900101300123", "organization_name": "ТОО Новая",
             "arrival_date": self.today.isoformat(), "departure_date": (self.today + timedelta(days=3)).isoformat()}
        p.update(person)
        data = {"purpose": "Ремонт", "contact_name": "Отв", "contact_phone": "+7", "comment": "",
                "visitors-TOTAL_FORMS": "1", "visitors-INITIAL_FORMS": "0",
                "visitors-MIN_NUM_FORMS": "0", "visitors-MAX_NUM_FORMS": "500"}
        for k, v in p.items():
            if v is True:
                v = "on"
            if v is not False:
                data[f"visitors-0-{k}"] = v
        return data

    def test_create_request(self):
        self.login(self.applicant)
        resp = self.client.post(reverse("request_create"), self.post_data(needs_camp=True, needs_meals=True, lunch=True))
        self.assertEqual(resp.status_code, 302)
        v = Visitor.objects.get()
        self.assertTrue(v.needs_camp and v.lunch and not v.breakfast)
        self.assertEqual(v.organization.name, "ТОО Новая")
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.REQUEST_CREATE).exists())
        # ИИН не пишется в журнал
        self.assertNotIn("900101300123", str(list(AuditLog.objects.values_list("changes", flat=True))))

    def test_one_day_disables_camp_and_sets_departure(self):
        self.login(self.applicant)
        self.client.post(reverse("request_create"), self.post_data(one_day=True, needs_camp=True, departure_date=""))
        v = Visitor.objects.get()
        self.assertFalse(v.needs_camp)
        self.assertEqual(v.departure_date, v.arrival_date)

    def test_meals_require_choice(self):
        self.login(self.applicant)
        resp = self.client.post(reverse("request_create"), self.post_data(needs_meals=True))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Visitor.objects.exists())

    def test_iin_validation(self):
        self.login(self.applicant)
        resp = self.client.post(reverse("request_create"), self.post_data(doc_number="123"))
        self.assertContains(resp, "12 цифр")

    def test_cannot_edit_others_request(self):
        v = self.visitor()
        self.login(self.other)
        self.assertEqual(self.client.get(reverse("request_edit", args=[v.request_id])).status_code, 404)

    def test_cancel_request(self):
        v = self.visitor()
        self.login(self.applicant)
        self.client.post(reverse("request_cancel", args=[v.request_id]))
        v.refresh_from_db()
        self.assertEqual(v.status, Visitor.Status.CANCELLED)
        self.assertTrue(v.request.is_cancelled)


class KppTests(Base):
    def test_arrive_depart_undo(self):
        v = self.visitor(vehicle_plate="123 abc 12")
        self.login(self.security)
        self.client.post(reverse("kpp_action", args=[v.pk, "arrive"]))
        v.refresh_from_db()
        self.assertEqual(v.status, Visitor.Status.ARRIVED)
        self.assertEqual(v.arrived_by, self.security)
        self.assertEqual(v.arrived_checkpoint, self.cp)
        # повторное нажатие ничего не ломает
        self.client.post(reverse("kpp_action", args=[v.pk, "arrive"]))
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.ARRIVE).count(), 1)
        self.client.post(reverse("kpp_action", args=[v.pk, "depart"]))
        v.refresh_from_db()
        self.assertEqual(v.status, Visitor.Status.DEPARTED)
        self.client.post(reverse("kpp_action", args=[v.pk, "undo"]))
        v.refresh_from_db()
        self.assertEqual(v.status, Visitor.Status.ARRIVED)

    def test_search_by_plate_in_cyrillic_and_name_case(self):
        self.visitor(vehicle_plate="123ABC12", full_name="Сидоров Олег")
        self.login(self.security)
        self.assertContains(self.client.get(reverse("kpp"), {"q": "123 авс"}), "Сидоров Олег")
        self.assertContains(self.client.get(reverse("kpp"), {"q": "сидоров"}), "Сидоров Олег")

    def test_no_show_highlighted(self):
        self.visitor(arrival_date=self.today - timedelta(days=1), full_name="Опоздавший")
        self.login(self.security)
        resp = self.client.get(reverse("kpp"))
        self.assertContains(resp, "Не прибыли в назначенный день")
        self.assertContains(resp, "st-noshow")

    def test_tomorrow_tab(self):
        self.visitor(arrival_date=self.today + timedelta(days=1), departure_date=self.today + timedelta(days=2),
                     full_name="Завтрашний")
        self.login(self.security)
        self.assertNotContains(self.client.get(reverse("kpp")), "Завтрашний")
        self.assertContains(self.client.get(reverse("kpp"), {"tab": "tomorrow"}), "Завтрашний")

    def test_roles_cannot_mark(self):
        v = self.visitor()
        for user in (self.applicant, self.camp):
            self.login(user)
            self.assertEqual(self.client.post(reverse("kpp_action", args=[v.pk, "arrive"])).status_code, 403)


class CampTests(Base):
    def test_only_camp_visitors_and_no_documents(self):
        self.visitor(full_name="С кэмпом", needs_camp=True)
        self.visitor(full_name="Без кэмпа")
        self.login(self.camp)
        resp = self.client.get(reverse("camp"))
        self.assertContains(resp, "С кэмпом")
        self.assertNotContains(resp, "Без кэмпа")
        self.assertNotContains(resp, "900101300123")

    def test_check_in_out(self):
        v = self.visitor(needs_camp=True)
        self.login(self.camp)
        self.client.post(reverse("camp_action", args=[v.pk, "check_in"]), {"building": "К2", "room": "214"})
        v.refresh_from_db()
        self.assertEqual((v.camp_status, v.camp_building, v.camp_room), (Visitor.CampStatus.CHECKED_IN, "К2", "214"))
        self.client.post(reverse("camp_action", args=[v.pk, "check_out"]))
        v.refresh_from_db()
        self.assertEqual(v.camp_status, Visitor.CampStatus.CHECKED_OUT)

    def test_waiting_people_first(self):
        settled = self.visitor(full_name="Заселённый", needs_camp=True, status=Visitor.Status.ARRIVED,
                               arrived_at=timezone.now(), camp_status=Visitor.CampStatus.CHECKED_IN)
        not_yet = self.visitor(full_name="Ещё в пути", needs_camp=True)
        at_gate = self.visitor(full_name="Прошёл КПП", needs_camp=True, status=Visitor.Status.ARRIVED,
                               arrived_at=timezone.now())
        self.login(self.camp)
        items = self.client.get(reverse("camp")).context["sections"][0]["items"]
        self.assertEqual([v.pk for v in items], [at_gate.pk, not_yet.pk, settled.pk])

    def test_one_day_not_in_camp(self):
        v = self.visitor(one_day=True, needs_camp=True, departure_date=self.today)
        v.clean()
        self.assertFalse(v.needs_camp)


class CanteenTests(Base):
    def test_camp_account_sees_camp_canteen_and_reports(self):
        self.login(self.camp)
        for name in ("camp", "canteen", "reports"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertEqual(self.client.get(reverse("kpp")).status_code, 403)

    @override_settings(NOTIFICATIONS_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_one_mail_for_camp_and_meals(self):
        self.camp.email = "camp@example.com"
        self.camp.save()
        v = self.visitor(full_name="С питанием", needs_meals=True, lunch=True)
        self.visitor(request=v.request, full_name="С кэмпом", needs_camp=True)
        self.visitor(request=v.request, full_name="Только КПП")
        with self.captureOnCommitCallbacks(execute=True):
            notify_request(v.request, "create")
        sent = [m for m in mail.outbox if m.to == ["camp@example.com"]]
        self.assertEqual(len(sent), 1)
        self.assertIn("С питанием", sent[0].body)
        self.assertIn("С кэмпом", sent[0].body)
        self.assertNotIn("Только КПП", sent[0].body)

    def test_counts(self):
        self.visitor(needs_meals=True, breakfast=True, lunch=True, dinner=True)
        self.visitor(needs_meals=True, lunch=True, one_day=True, departure_date=self.today)
        self.visitor(full_name="Без питания")
        self.login(self.camp)
        resp = self.client.get(reverse("canteen"))
        self.assertEqual(resp.context["day_summary"], {"breakfast": 1, "lunch": 2, "dinner": 1, "people": 2})
        self.assertNotContains(resp, "Без питания")


class ImportTests(Base):
    def test_parse(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["Список бригады"])
        ws.append(["ФИО", "ИИН", "Организация", "Дата прибытия", "Обед"])
        ws.append(["Алиев Али", 10101300123, "ТОО А", "25.10.2026", "да"])
        ws.append([None, None, None, None, None])
        ws.append(["Smith John", "N123", "ТОО А", None, None])
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        rows, errors = parse_people(buf)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["doc_number"], "010101300123")  # восстановлен ведущий ноль
        self.assertEqual(rows[0]["arrival_date"], "2026-10-25")
        self.assertTrue(rows[0]["needs_meals"] and rows[0]["lunch"])
        self.assertEqual(rows[1]["doc_type"], Visitor.DocType.PASSPORT)
        self.assertNotIn("needs_meals", rows[1])  # пустой «Обед» не отключает питание из формы

    def test_upload_endpoint(self):
        wb = Workbook()
        wb.active.append(["ФИО"])
        wb.active.append(["Тестов Тест"])
        buf = BytesIO()
        wb.save(buf)
        self.login(self.applicant)
        resp = self.client.post(reverse("import_parse"), {"file": SimpleUploadedFile("a.xlsx", buf.getvalue())})
        self.assertEqual(resp.json()["rows"][0]["full_name"], "Тестов Тест")


class ReportTests(Base):
    def test_reports_by_role(self):
        self.visitor(needs_meals=True, lunch=True)
        period = {"date_from": self.today.isoformat(), "date_to": self.today.isoformat()}
        self.login(self.camp)
        resp = self.client.get(reverse("reports"), {"kind": "meals", **period})
        self.assertEqual(resp["Content-Type"].split(";")[0], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        # кэмп/столовая не может выгрузить отчёт КПП (с документами)
        resp = self.client.get(reverse("reports"), {"kind": "movements", **period})
        self.assertEqual(resp["Content-Type"].split(";")[0], "text/html")


class TransportTests(Base):
    NEW_CAR = {"new-vehicle_make": "Hyundai County", "new-vehicle_plate": "045 krs 12", "new-driver_name": "Водитель"}

    def assign(self, people, direction="in", day=None, **extra):
        day = day or self.today
        data = {"direction": direction, "date": day.isoformat(), "visitor": [v.pk for v in people], **extra}
        return self.client.post(reverse("dispatch_assign"), data)

    def test_dispatcher_assigns_car_and_kpp_sees_it(self):
        a = self.visitor(full_name="Пассажир Один", needs_transport=True)
        b = self.visitor(full_name="Пассажир Два", needs_transport=True, arrival_date=self.today + timedelta(days=1),
                         departure_date=self.today + timedelta(days=3))
        self.visitor(full_name="На своей машине")
        self.login(self.dispatcher)
        resp = self.client.get(reverse("dispatch"))
        self.assertContains(resp, "Пассажир Один")
        self.assertNotContains(resp, "На своей машине")
        self.assign([a, b], **self.NEW_CAR)  # b на другую дату — не попадает в машину
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.arrival_trip.vehicle_plate, "045KRS12")
        self.assertIsNone(b.arrival_trip)

        self.login(self.security)
        resp = self.client.get(reverse("kpp"), {"q": "045KRS"})
        self.assertContains(resp, "Пассажир Один")  # поиск по номеру машины диспетчера
        self.assertContains(resp, "Машина въехала")

    def test_kpp_and_camp_wait_for_car(self):
        """Пока диспетчер не назначил машину, человека не видят ни КПП, ни кэмп."""
        v = self.visitor(full_name="Ждёт машину", needs_transport=True, needs_camp=True)
        for user, url in ((self.security, "kpp"), (self.camp, "camp")):
            self.login(user)
            self.assertNotContains(self.client.get(reverse(url)), "Ждёт машину")
        self.login(self.dispatcher)
        self.assign([v], **self.NEW_CAR)
        for user, url in ((self.security, "kpp"), (self.camp, "camp")):
            self.login(user)
            self.assertContains(self.client.get(reverse(url)), "Ждёт машину")

    def test_date_change_drops_trip(self):
        v = self.visitor(needs_transport=True)
        self.login(self.dispatcher)
        self.assign([v], **self.NEW_CAR)
        v.refresh_from_db()
        self.assertIsNotNone(v.arrival_trip)
        v.arrival_date = self.today + timedelta(days=1)
        v.save()
        self.assertIsNone(Visitor.objects.get(pk=v.pk).arrival_trip)

    def test_departure_trip(self):
        v = self.visitor(needs_transport=True, status=Visitor.Status.ARRIVED, arrived_at=timezone.now(),
                         departure_date=self.today)
        self.login(self.dispatcher)
        self.assertContains(self.client.get(reverse("dispatch"), {"tab": "out"}), v.full_name)
        self.assign([v], direction="out", **self.NEW_CAR)
        v.refresh_from_db()
        self.assertEqual(v.departure_trip.direction, Trip.Direction.OUT)

    def test_kpp_marks_car_and_all_passengers(self):
        people = [self.visitor(full_name=f"П{i}", needs_transport=True) for i in range(3)]
        self.login(self.dispatcher)
        self.assign(people, **self.NEW_CAR)
        trip = Trip.objects.get()
        self.login(self.security)
        self.client.post(reverse("kpp_trip_action", args=[trip.pk, "enter"]))
        self.client.post(reverse("kpp_trip_action", args=[trip.pk, "passengers"]))
        self.assertEqual(Visitor.objects.filter(status=Visitor.Status.ARRIVED).count(), 3)
        self.client.post(reverse("kpp_trip_action", args=[trip.pk, "exit"]))
        trip.refresh_from_db()
        self.assertTrue(trip.entered_at and trip.exited_at)
        self.client.post(reverse("kpp_trip_action", args=[trip.pk, "undo"]))
        trip.refresh_from_db()
        self.assertIsNone(trip.exited_at)
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.TRIP_ENTER).count(), 1)

        # отмеченную машину диспетчер удалить не может
        self.login(self.dispatcher)
        self.client.post(reverse("trip_delete", args=[trip.pk]))
        self.assertTrue(Trip.objects.filter(pk=trip.pk).exists())

    def test_delete_trip_returns_people(self):
        v = self.visitor(needs_transport=True)
        self.login(self.dispatcher)
        self.assign([v], **self.NEW_CAR)
        self.client.post(reverse("trip_delete", args=[Trip.objects.get().pk]))
        v.refresh_from_db()
        self.assertIsNone(v.arrival_trip)

    def test_roles(self):
        self.login(self.dispatcher)
        self.assertEqual(self.client.get(reverse("kpp")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("dispatch")), "900101300123")
        self.login(self.security)
        self.assertEqual(self.client.get(reverse("dispatch")).status_code, 403)

    @override_settings(NOTIFICATIONS_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_dispatcher_mail(self):
        self.dispatcher.email = "disp@example.com"
        self.dispatcher.save()
        v = self.visitor(full_name="С транспортом", needs_transport=True)
        with self.captureOnCommitCallbacks(execute=True):
            notify_request(v.request, "create")
        sent = [m for m in mail.outbox if m.to == ["disp@example.com"]]
        self.assertEqual(len(sent), 1)
        self.assertIn("С транспортом", sent[0].body)


class ApplicantSeesTransportTests(TransportTests):
    CAR = {**TransportTests.NEW_CAR, "new-time": "14:30", "new-pickup_place": "У главного входа в офис",
           "new-driver_phone": "+7 705 111 22 33"}

    def test_request_page_shows_car_place_time_phone(self):
        v = self.visitor(full_name="Пассажир", needs_transport=True)
        self.login(self.dispatcher)
        self.assign([v], **self.CAR)
        self.login(self.applicant)
        resp = self.client.get(reverse("request_detail", args=[v.request.pk]))
        for text in ("У главного входа в офис", "14:30", "045KRS12", "+7 705 111 22 33"):
            self.assertContains(resp, text)
        self.assertContains(self.client.get(reverse("request_list")), "привоз назначен")  # вывоз — позже

    def test_request_list_shows_waiting(self):
        self.visitor(needs_transport=True)
        self.login(self.applicant)
        self.assertContains(self.client.get(reverse("request_list")), "ждёт машину")

    @override_settings(NOTIFICATIONS_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_applicant_mail_on_assign_and_change(self):
        self.applicant.email = "app@example.com"
        self.applicant.save()
        v = self.visitor(full_name="Пассажир", needs_transport=True)
        self.login(self.dispatcher)
        with self.captureOnCommitCallbacks(execute=True):
            self.assign([v], **self.CAR)
        sent = [m for m in mail.outbox if m.to == ["app@example.com"]]
        self.assertEqual(len(sent), 1)
        self.assertIn("Место подачи: У главного входа в офис", sent[0].body)
        self.assertIn("в 14:30", sent[0].body)

        trip = Trip.objects.get()
        data = {"time": "15:00", "pickup_place": "У КПП-1", "vehicle_make": trip.vehicle_make,
                "vehicle_plate": trip.vehicle_plate, "driver_name": trip.driver_name, "driver_phone": trip.driver_phone}
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("trip_edit", args=[trip.pk]), data)
        sent = [m for m in mail.outbox if m.to == ["app@example.com"]]
        self.assertEqual(len(sent), 2)
        self.assertIn("Изменён транспорт", sent[1].subject)
        self.assertIn("У КПП-1", sent[1].body)


class ApprovalTests(Base):
    def setUp(self):
        super().setUp()
        self.chief = make_user("chief", R.CHIEF)

    def pending(self, **kw):
        req = Request.objects.create(author=self.applicant, purpose="Подрядные работы", contact_name="Отв",
                                     contact_phone="1")
        return req, self.visitor(request=req, **kw)

    def test_new_request_is_pending_and_hidden(self):
        self.login(self.applicant)
        resp = self.client.post(reverse("request_create"), RequestFormTests.post_data(self, needs_camp=True), follow=True)
        self.assertContains(resp, "отправлена на согласование")
        self.assertEqual(Request.objects.get().approval, Request.Approval.PENDING)
        for user, url in ((self.security, "kpp"), (self.camp, "camp"), (self.dispatcher, "dispatch")):
            self.login(user)
            self.assertNotContains(self.client.get(reverse(url)), "Петров Пётр")
        self.login(self.security)  # и отметить «Прибыл» в обход экрана тоже нельзя
        self.client.post(reverse("kpp_action", args=[Visitor.objects.get().pk, "arrive"]))
        self.assertEqual(Visitor.objects.get().status, Visitor.Status.EXPECTED)

    def test_chief_approves_with_struck_person(self):
        req, a = self.pending(full_name="Допущен Первый")
        b = self.visitor(request=req, full_name="Недопущен Второй")
        self.login(self.chief)
        self.assertContains(self.client.get(reverse("approval_list")), "Подрядные работы")
        resp = self.client.get(reverse("request_detail", args=[req.pk]))
        self.assertContains(resp, "900101300123")  # начальник видит документы
        self.assertNotContains(resp, reverse("request_edit", args=[req.pk]))  # но не правит
        resp = self.client.post(reverse("request_approve", args=[req.pk]), {"shown": [a.pk, b.pk], "admit": [a.pk]},
                                follow=True)
        self.assertContains(resp, "Не допущены: Недопущен Второй")
        req.refresh_from_db()
        b.refresh_from_db()
        self.assertTrue(req.is_approved)
        self.assertEqual(req.approved_by, self.chief)
        self.assertEqual(b.status, Visitor.Status.REJECTED)
        self.login(self.security)
        resp = self.client.get(reverse("kpp"))
        self.assertContains(resp, "Допущен Первый")
        self.assertNotContains(resp, "Недопущен Второй")

    def test_approve_refused_if_request_changed(self):
        req, a = self.pending()
        self.visitor(request=req, full_name="Добавлен позже")
        self.login(self.chief)
        self.client.post(reverse("request_approve", args=[req.pk]), {"shown": [a.pk], "admit": [a.pk]})
        req.refresh_from_db()
        self.assertTrue(req.is_pending)

    def test_reject_and_resubmit(self):
        req, v = self.pending()
        self.login(self.chief)
        self.client.post(reverse("request_reject", args=[req.pk]), {"reason": ""})
        req.refresh_from_db()
        self.assertTrue(req.is_pending)  # без причины не отклонить
        self.client.post(reverse("request_reject", args=[req.pk]), {"reason": "Нет допуска"})
        req.refresh_from_db()
        self.assertTrue(req.is_rejected)
        self.login(self.applicant)
        self.assertContains(self.client.get(reverse("request_detail", args=[req.pk])), "Нет допуска")
        data = RequestFormTests.post_data(self)
        data.update({"visitors-INITIAL_FORMS": "1", "visitors-0-id": str(v.pk), "visitors-0-request": str(req.pk),
                     "visitors-0-full_name": "Иванов Иван"})
        resp = self.client.post(reverse("request_edit", args=[req.pk]), data)
        self.assertEqual(resp.status_code, 302)
        req.refresh_from_db()
        self.assertTrue(req.is_pending)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.REQUEST_RESUBMIT).exists())

    def test_only_chief_decides(self):
        req, v = self.pending()
        for user in (self.applicant, self.security, self.dispatcher):
            self.login(user)
            self.assertEqual(self.client.post(reverse("request_approve", args=[req.pk]),
                                              {"shown": [v.pk], "admit": [v.pk]}).status_code, 403)
        req.refresh_from_db()
        self.assertTrue(req.is_pending)

    @override_settings(NOTIFICATIONS_ENABLED=True)
    def test_emails_follow_approval(self):
        self.chief.email, self.security.email, self.applicant.email = "c@kbm.kz", "s@kbm.kz", "a@kbm.kz"
        for u in (self.chief, self.security, self.applicant):
            u.save()
        req, v = self.pending()
        with self.captureOnCommitCallbacks(execute=True):
            notify_request(req, "create")
        self.assertEqual([m.to for m in mail.outbox], [["c@kbm.kz"]])
        mail.outbox.clear()
        self.login(self.chief)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("request_approve", args=[req.pk]), {"shown": [v.pk], "admit": [v.pk]})
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["a@kbm.kz", "s@kbm.kz"])


class CargoTests(Base):
    def form_data(self, **cargo):
        data = RequestFormTests.post_data(self)
        data.update({"cargo-has_cargo": "on", "cargo-date": self.today.isoformat(), "cargo-vehicle_make": "КАМАЗ",
                     "cargo-vehicle_plate": "512 brs 12", "cargo-driver_name": "Водитель", "cargo-cargo": "Трубы 120 шт."})
        data.update(cargo)
        return data

    def test_cargo_required_fields(self):
        self.login(self.applicant)
        resp = self.client.post(reverse("request_create"), self.form_data(**{"cargo-cargo": ""}))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Request.objects.exists())

    def test_cargo_visible_at_kpp_after_approval(self):
        self.login(self.applicant)
        self.client.post(reverse("request_create"), self.form_data())
        trip = Trip.objects.get()
        self.assertEqual((trip.direction, trip.vehicle_plate), (Trip.Direction.CARGO, "512BRS12"))
        self.login(self.security)
        self.assertNotContains(self.client.get(reverse("kpp"), {"tab": "trips"}), "512BRS12")
        Request.objects.update(approval=Request.Approval.APPROVED)
        resp = self.client.get(reverse("kpp"), {"tab": "trips"})
        self.assertContains(resp, "Трубы 120 шт.")
        self.client.post(reverse("kpp_trip_action", args=[trip.pk, "enter"]))
        trip.refresh_from_db()
        self.assertIsNotNone(trip.entered_at)
        self.login(self.dispatcher)  # диспетчер груз не видит и не правит
        self.assertNotContains(self.client.get(reverse("dispatch")), "512BRS12")
        self.assertEqual(self.client.get(reverse("trip_edit", args=[trip.pk])).status_code, 404)

    def test_uncheck_removes_cargo(self):
        self.login(self.applicant)
        self.client.post(reverse("request_create"), self.form_data())
        req, v = Request.objects.get(), Visitor.objects.get()
        data = self.form_data(**{"cargo-has_cargo": ""})
        data.update({"visitors-INITIAL_FORMS": "1", "visitors-0-id": str(v.pk), "visitors-0-request": str(req.pk)})
        self.client.post(reverse("request_edit", args=[req.pk]), data)
        self.assertFalse(Trip.objects.exists())
