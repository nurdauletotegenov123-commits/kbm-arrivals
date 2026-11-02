from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from accounts.media import protected_media
from permits.api_auth import CustomUserToken, SessionToken
from permits.spa import permits_app
from permits.user_views import UserSearchViewSet
from permits.views import KmgPermitsExportView

users_router = DefaultRouter()
users_router.register(r"users", UserSearchViewSet, basename="users")

urlpatterns = [
    path("", include("accounts.urls")),
    path("", include("visits.urls")),
    path("audit/", include("audit.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("django-admin/", admin.site.urls),
    # --- Наряд-допуски: интерфейс и API ---
    path("permits/", permits_app, name="permits_app"),
    path("api/v1/", include("permits.urls")),
    path("api/v1/api-token-auth/", CustomUserToken.as_view()),
    path("api/v1/session-token/", SessionToken.as_view()),
    path("api/v1/", include(users_router.urls)),
    path("api/v1/integration/kmg/permits/", KmgPermitsExportView.as_view()),
    path("api/v1/egov_qr/", include("egov_qr.urls")),
    path("permits_scans/<path:path>", protected_media, name="protected_media"),
]
