from datetime import date, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from accounts.models import Checkpoint, User
from audit.models import AuditLog
from visits.models import EntryBan

from . import sample, services
from .importer import apply_plan, parse_dates, parse_plan, parse_plates
from .models import Delivery

R = User.Role


def make_user(username, role, **kw):
    u = User.objects.create(username=username, role=role, full_name=username, **kw)
    u.set_password("pass12345")
    u.save()
    return u


class ParseTests(TestCase):
    def test_dates(self):
        plan = date(2026, 11, 1)
        cases = {
            "31.10.2026/ 03.11.2026": ("2026-10-31", "2026-11-03"),
            "31.10.-01.11.2026.": ("2026-10-31", "2026-11-01"),
            "30.10-01.11.2026.": ("2026-10-30", "2026-11-01"),
            "01.11.2026г. -05.11.2026г.": ("2026-11-01", "2026-11-05"),
            "31.10.2026 по 01.11.2026": ("2026-10-31", "2026-11-01"),
            "10/31/2026": ("2026-10-31", "2026-10-31"),
            "11/3/2026": ("2026-11-03", "2026-11-03"),
            "03/11/2026-06/11/2026": ("2026-11-03", "2026-11-06"),
            "02-03.11.2026": ("2026-11-02", "2026-11-03"),
            "03.11-04.11.2026": ("2026-11-03", "2026-11-04"),
        }
        for text, (a, b) in cases.items():
            with self.subTest(text):
                d1, d2 = parse_dates(text, plan)
                self.assertEqual((d1.isoformat(), d2.isoformat()), (a, b))

    def test_plates(self):
        self.assertEqual(parse_plates("046LEX15 40PAZ15"), ("046LEX15", "40PAZ15"))
        self.assertEqual(parse_plates("808XXA17/ прицеп KRONE SD 97AMH17"), ("808XXA17", "97AMH17"))
        self.assertEqual(parse_plates("KZ 585AFU 12"), ("585AFU12", ""))
        self.assertEqual(parse_plates("797AOJ/01"), ("797AOJ01", ""))
        self.assertEqual(parse_plates("202 AKK 12"), ("202AKK12", ""))
        self.assertEqual(parse_plates("гос. номер 565AAG04, прицеп гос. номер 14AAO04"), ("565AAG04", "14AAO04"))

    def test_sample_plan(self):
        day = date(2026, 11, 4)
        deliveries, warnings = parse_plan(sample.build(day))
        self.assertEqual(warnings, [])
        self.assertEqual(len(deliveries), 5)
        first = deliveries[0]
        self.assertEqual((first["truck_plate"], first["trailer_plate"]), ("046LEX15", "40PAZ15"))
        self.assertEqual((first["date_from"], first["date_to"]), ("2026-11-03", "2026-11-05"))
        self.assertEqual(len(first["items"]), 2)  # объединённые ячейки: одна машина — две позиции
        self.assertEqual(first["items"][1]["contract"], "№ 1145-ЗТ/26")
        self.assertTrue(first["items"][0]["is_otp"])
        self.assertEqual(first["contact_phone"], "+7 701 555 12 34")
        self.assertEqual(first["driver_name"], "Сейткалиев Е.М.")
        self.assertEqual(deliveries[3]["date_from"], "2026-11-05")  # американская дата 11/5/2026


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.cp = Checkpoint.objects.create(name="КПП-1")
        self.supply = make_user("snab", R.SUPPLY)
        self.security = make_user("sec", R.SECURITY, checkpoint=self.cp)
        self.chief = make_user("chief", R.CHIEF)
        self.applicant = make_user("app", R.APPLICANT)

    def load_sample(self):
        deliveries, _w = parse_plan(sample.build(self.today))
        return apply_plan(deliveries, self.supply)


class ApplyTests(Base):
    def test_reload_does_not_duplicate(self):
        self.assertEqual(self.load_sample(), {"created": 5, "updated": 0, "items": 6})
        self.assertEqual(self.load_sample(), {"created": 0, "updated": 5, "items": 0})
        self.assertEqual(Delivery.objects.count(), 5)

    def test_arrived_note_and_search(self):
        self.load_sample()
        d = Delivery.objects.get(truck_plate="202AKK12")
        self.assertTrue(d.marked_arrived_in_plan)
        self.assertEqual(d.state[0], "arrived")
        self.assertIn("ингибитор", Delivery.objects.get(truck_plate="797AOJ01").search_index)


class ViewTests(Base):
    def test_import_preview_confirm(self):
        self.client.force_login(self.supply)
        f = sample.build(self.today)
        resp = self.client.post(reverse("delivery_import"), {"file": SimpleUploadedFile("plan.xlsx", f.read())})
        self.assertRedirects(resp, reverse("delivery_import_preview"))
        resp = self.client.get(reverse("delivery_import_preview"))
        self.assertContains(resp, "046LEX15")
        self.assertEqual(Delivery.objects.count(), 0)  # до подтверждения ничего не загружено
        self.client.post(reverse("delivery_import_preview"), {"action": "apply"})
        self.assertEqual(Delivery.objects.count(), 5)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.DELIVERY_IMPORT).exists())

    def test_bad_file(self):
        self.client.force_login(self.supply)
        resp = self.client.post(reverse("delivery_import"), {"file": SimpleUploadedFile("plan.pdf", b"%PDF")})
        self.assertContains(resp, "Нужен файл Excel")

    def test_manual_create(self):
        self.client.force_login(self.supply)
        resp = self.client.post(reverse("delivery_create"), {
            "date_from": self.today.isoformat(), "date_to": "", "truck_plate": "123 abc 02", "vehicle": "КАМАЗ",
            "items-TOTAL_FORMS": "2", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "1", "items-MAX_NUM_FORMS": "100",
            "items-0-name": "Задвижка 30с41нж", "items-0-quantity": "4 шт", "items-0-abp": "ПО", "items-0-is_otp": "true",
            "items-1-name": "Фланец", "items-1-quantity": "8 шт",
        })
        d = Delivery.objects.get()
        self.assertRedirects(resp, reverse("delivery_detail", args=[d.pk]))
        self.assertEqual((d.truck_plate, d.date_to, d.items.count()), ("123ABC02", self.today, 2))
        self.assertTrue(d.items.get(name__startswith="Задвижка").is_otp)
        self.assertIn("задвижка", Delivery.objects.get().search_index)

    def test_manual_needs_item(self):
        self.client.force_login(self.supply)
        resp = self.client.post(reverse("delivery_create"), {
            "date_from": self.today.isoformat(), "truck_plate": "123ABC02",
            "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "1", "items-MAX_NUM_FORMS": "100",
            "items-0-name": "",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Delivery.objects.exists())

    def test_board_and_access(self):
        self.load_sample()
        for user in (self.supply, self.chief, self.security):
            self.client.force_login(user)
            resp = self.client.get(reverse("delivery_board"))
            self.assertContains(resp, "808XXA17")
            self.assertNotContains(resp, "797AOJ01")  # завтрашняя
        self.client.force_login(self.chief)
        self.assertEqual(self.client.get(reverse("delivery_create")).status_code, 403)
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.get(reverse("delivery_board")).status_code, 403)

    def test_board_search_and_filter(self):
        self.load_sample()
        self.client.force_login(self.supply)
        resp = self.client.get(reverse("delivery_board"), {"q": "ингибитор"})
        self.assertContains(resp, "797AOJ01")
        resp = self.client.get(reverse("delivery_board"), {"abp": "сгм"})
        self.assertContains(resp, "808XXA17")
        self.assertNotContains(resp, "585AFU12")

    def test_export(self):
        self.load_sample()
        d = Delivery.objects.get(truck_plate="808XXA17")
        services.enter(d.pk, self.security)
        self.client.force_login(self.chief)
        resp = self.client.get(reverse("delivery_export"), {"from": self.today.isoformat(), "to": self.today.isoformat()})
        ws = load_workbook(__import__("io").BytesIO(resp.content)).active
        rows = [[c.value for c in r] for r in ws.iter_rows(min_row=4)]
        row = next(r for r in rows if "808XXA17" in (r[7] or ""))
        self.assertTrue(row[-1].startswith("Прибыл"))


class KppTests(Base):
    def test_kpp_cargo_tab_and_marks(self):
        self.load_sample()
        self.client.force_login(self.security)
        resp = self.client.get(reverse("kpp"), {"tab": "cargo"})
        self.assertContains(resp, "808XXA17")
        self.assertContains(resp, "046LEX15")  # диапазон дат включает сегодня
        self.assertNotContains(resp, "797AOJ01")
        d = Delivery.objects.get(truck_plate="808XXA17")
        self.client.post(reverse("delivery_kpp_action", args=[d.pk, "enter"]))
        d.refresh_from_db()
        self.assertEqual((d.status, d.entered_checkpoint), (Delivery.Status.ARRIVED, self.cp))
        self.client.post(reverse("delivery_kpp_action", args=[d.pk, "exit"]))
        d.refresh_from_db()
        self.assertEqual(d.status, Delivery.Status.DEPARTED)
        self.client.post(reverse("delivery_kpp_action", args=[d.pk, "undo"]))
        d.refresh_from_db()
        self.assertEqual(d.status, Delivery.Status.ARRIVED)
        self.assertEqual(AuditLog.objects.filter(object_type="delivery").count(), 3)

    def test_kpp_search_by_trailer_and_ban(self):
        self.load_sample()
        EntryBan.objects.create(vehicle_plate="808XXA17", reason="Нарушение ТБ", created_by=self.chief)
        self.client.force_login(self.security)
        resp = self.client.get(reverse("kpp"), {"q": "97amh17"})
        self.assertContains(resp, "808XXA17")
        self.assertContains(resp, "Нарушение ТБ")

    def test_off_schedule_and_no_show(self):
        d = Delivery.objects.create(truck_plate="111AAA01", date_from=self.today + timedelta(days=2),
                                    date_to=self.today + timedelta(days=2))
        services.enter(d.pk, self.security)
        self.assertEqual(Delivery.objects.get(pk=d.pk).state[0], "offschedule")
        late = Delivery.objects.create(truck_plate="222BBB01", date_from=self.today - timedelta(days=3),
                                       date_to=self.today - timedelta(days=1))
        self.assertEqual(late.state[0], "noshow")

    def test_double_enter(self):
        d = Delivery.objects.create(truck_plate="111AAA01", date_from=self.today, date_to=self.today)
        services.enter(d.pk, self.security)
        from visits.services import ActionError
        with self.assertRaises(ActionError):
            services.enter(d.pk, self.security)
        self.client.force_login(self.supply)
        self.client.post(reverse("delivery_cancel", args=[d.pk]))
        self.assertEqual(Delivery.objects.get(pk=d.pk).status, Delivery.Status.ARRIVED)
