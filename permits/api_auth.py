"""Вход интерфейса нарядов (React). Портал: тот же пользователь, что и на сайте; токен API выдаётся
по логину и паролю (доменный — через AD) или сразу по уже открытой сессии портала (без повторного входа)."""
from django.conf import settings
from django.middleware.csrf import get_token
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def user_payload(user, token):
    return {
        "token": token.key,
        "id": user.pk,
        "username": user.username,
        "name": user.get_full_name(),
        "email": user.email,
        "role": user.permit_role,
        "company": user.company_name,
        "department": str(user.department) if user.department else "Не указано",
        "position": user.position,
        "is_admin": user.is_admin,
        "is_auditor": user.is_auditor,
        # ИИН/БИН нужны интерфейсу для подписания через NCALayer
        "iin": user.iin,
        "bin": user.bin,
        "ai_enabled": bool(settings.OPENCODE_ZEN_API_KEY),
    }


def portal_payload(request):
    """Шапка портала внутри интерфейса нарядов: те же пункты меню, имя, роль — как на остальных страницах."""
    from accounts.context_processors import nav

    ctx = nav(request)
    here = reverse("permits_app")
    return {
        "nav": [{"url": i["url"], "label": str(i["label"]), "active": i["url"] == here} for i in ctx.get("nav_items", [])],
        "home": reverse("home"),
        "profile": reverse("own_password"),
        "logout": reverse("logout"),
        "csrf": get_token(request),
        "user": request.user.display_name,
        "role_label": str(ctx.get("role_label", "")),
    }


class CustomUserToken(ObtainAuthToken):
    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _created = Token.objects.get_or_create(user=user)
        return Response(user_payload(user, token))


class SessionToken(APIView):
    """GET: пользователь уже вошёл на портал (сессия) — отдать токен API, чтобы интерфейс нарядов не спрашивал пароль."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        token, _created = Token.objects.get_or_create(user=request.user)
        return Response({**user_payload(request.user, token), "portal": portal_payload(request)})
