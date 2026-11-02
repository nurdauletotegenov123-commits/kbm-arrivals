"""Модуль «Наряд-допуски» внутри портала: общий вход, защищённые файлы, видимость нарядов, главная."""
import tempfile
from datetime import timedelta
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from permits.models import ApprovalStep, WorkPermit, WorkPermitTemplate

MEDIA = tempfile.mkdtemp(prefix="kbm-permits-")


def user(username, role=User.Role.APPLICANT, permit_role=User.PermitRole.WORK_PRODUCER, **kw):
    u = User.objects.create(username=username, role=role, permit_role=permit_role, full_name=f"Тестов {username}", **kw)
    u.set_password("x-pass-123")
    u.save()
    return u


def permit(initiator, **data):
    tpl = WorkPermitTemplate.objects.get_or_create(name="Наряд повышенной опасности")[0]
    now = timezone.now()
    return WorkPermit.objects.create(initiator=initiator, template=tpl, valid_from=now, valid_to=now + timedelta(days=1),
                                     data={"category": "DANGEROUS", "workName": "Огневые работы", **data})


class SessionTokenTests(TestCase):
    def test_anonymous_gets_no_token_and_app_asks_login(self):
        self.assertIn(self.client.get("/api/v1/session-token/").status_code, (401, 403))
        self.assertEqual(self.client.get(reverse("permits_app")).status_code, 302)

    def test_portal_user_gets_token_permit_role_and_portal_menu(self):
        u = user("issuer", permit_role=User.PermitRole.ISSUER, iin="900101300123")
        self.client.force_login(u)
        data = self.client.get("/api/v1/session-token/").json()
        self.assertEqual((data["role"], data["iin"], data["username"]), ("ISSUER", "900101300123", "issuer"))
        self.assertTrue(data["token"])
        nav = {i["label"]: i["active"] for i in data["portal"]["nav"]}
        self.assertTrue(nav["Наряды"])
        self.assertFalse(data["ai_enabled"])
        # тем же токеном работает API нарядов
        resp = self.client.get("/api/v1/permits/", HTTP_AUTHORIZATION=f"Token {data['token']}")
        self.assertEqual(resp.status_code, 200)


class VisibilityTests(TestCase):
    def test_team_member_sees_permit_on_sqlite_too(self):
        author, member, stranger = user("author"), user("member"), user("stranger")
        p = permit(author, teamMembers=[{"userId": member.pk, "name": "Член бригады"}])
        for who, visible in ((author, True), (member, True), (stranger, False)):
            self.client.force_login(who)
            ids = [r["id"] for r in self.client.get("/api/v1/permits/").json()["results"]]
            self.assertEqual(p.pk in ids, visible, who.username)

    def test_portal_home_shows_permits_tile_with_pending_signatures(self):
        author, signer = user("author2"), user("signer", permit_role=User.PermitRole.ADMITTING)
        p = permit(author)
        WorkPermit.objects.filter(pk=p.pk).update(status="PENDING_APPROVAL")
        ApprovalStep.objects.create(permit=p, approver=signer, step_order=1, role="ADMITTING", status="PENDING")
        self.client.force_login(signer)
        resp = self.client.get(reverse("home"))
        self.assertContains(resp, "Наряд-допуски")
        self.assertContains(resp, "1 ждут вашей подписи")
        guard = user("guard", role=User.Role.SECURITY)
        self.client.force_login(guard)
        self.assertEqual(self.client.get(reverse("home")).url, reverse("kpp"))  # охрана — сразу на КПП


@override_settings(MEDIA_ROOT=MEDIA)
class ProtectedMediaTests(TestCase):
    def setUp(self):
        Path(MEDIA, "signatures").mkdir(parents=True, exist_ok=True)
        Path(MEDIA, "signatures", "s.png").write_bytes(b"png")
        Path(MEDIA, "cargo").mkdir(exist_ok=True)
        Path(MEDIA, "cargo", "c.pdf").write_bytes(b"pdf")

    def test_only_logged_in_and_never_cargo_or_outside(self):
        url = "/permits_scans/signatures/s.png"
        self.assertEqual(self.client.get(url).status_code, 302)  # без входа — на страницу входа
        self.client.force_login(user("viewer"))
        resp = self.client.get(url)
        self.assertEqual(b"".join(resp.streaming_content), b"png")
        self.assertEqual(self.client.get("/permits_scans/cargo/c.pdf").status_code, 404)  # у груза своя проверка прав
        self.assertEqual(self.client.get("/permits_scans/../settings.py").status_code, 404)


class UserSearchTests(TestCase):
    def test_search_by_name_returns_permit_role(self):
        user("n_otegenov", permit_role=User.PermitRole.ISSUER, position="Техник")
        self.client.force_login(user("someone"))
        rows = self.client.get("/api/v1/users/", {"search": "otegenov"}).json()
        self.assertEqual(rows[0]["role"], "ISSUER")
        self.assertEqual(rows[0]["position"], "Техник")
