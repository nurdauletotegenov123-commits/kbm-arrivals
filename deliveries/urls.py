from django.urls import path, re_path

from . import views

urlpatterns = [
    path("", views.board, name="delivery_board"),
    path("new/", views.create, name="delivery_create"),
    path("import/", views.import_upload, name="delivery_import"),
    path("import/preview/", views.import_preview, name="delivery_import_preview"),
    path("export.xlsx", views.export, name="delivery_export"),
    path("<int:pk>/", views.detail, name="delivery_detail"),
    path("<int:pk>/edit/", views.edit, name="delivery_edit"),
    path("<int:pk>/cancel/", views.cancel, name="delivery_cancel"),
    re_path(r"^(?P<pk>\d+)/kpp/(?P<action>enter|exit|undo)/$", views.kpp_action, name="delivery_kpp_action"),
]
