from django.urls import path, re_path

from . import views

urlpatterns = [
    path("overview/", views.dashboard, name="dashboard"),
    path("requests/", views.request_list, name="request_list"),
    path("requests/new/", views.request_create, name="request_create"),
    path("requests/import/", views.import_parse, name="import_parse"),
    path("requests/import/template.xlsx", views.import_template, name="import_template"),
    path("requests/<int:pk>/", views.request_detail, name="request_detail"),
    path("requests/<int:pk>/edit/", views.request_edit, name="request_edit"),
    path("requests/<int:pk>/cancel/", views.request_cancel, name="request_cancel"),
    path("kpp/", views.kpp, name="kpp"),
    re_path(r"^kpp/(?P<pk>\d+)/(?P<action>arrive|depart|undo)/$", views.kpp_action, name="kpp_action"),
    re_path(r"^kpp/trip/(?P<pk>\d+)/(?P<action>enter|exit|undo|passengers)/$", views.kpp_trip_action, name="kpp_trip_action"),
    path("dispatch/", views.dispatch, name="dispatch"),
    path("dispatch/assign/", views.dispatch_assign, name="dispatch_assign"),
    path("dispatch/trip/<int:pk>/", views.trip_edit, name="trip_edit"),
    path("dispatch/trip/<int:pk>/delete/", views.trip_delete, name="trip_delete"),
    path("dispatch/trip/<int:pk>/remove/<int:visitor_pk>/", views.trip_remove, name="trip_remove"),
    path("camp/", views.camp, name="camp"),
    re_path(r"^camp/(?P<pk>\d+)/(?P<action>check_in|check_out|room|undo)/$", views.camp_action, name="camp_action"),
    path("canteen/", views.canteen, name="canteen"),
    path("reports/", views.reports, name="reports"),
]
