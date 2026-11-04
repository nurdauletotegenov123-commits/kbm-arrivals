from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("accounts.urls")),
    path("", include("visits.urls")),
    path("deliveries/", include("deliveries.urls")),
    path("audit/", include("audit.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("django-admin/", admin.site.urls),
]
