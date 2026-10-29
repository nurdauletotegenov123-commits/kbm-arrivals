from django.contrib import admin

from .models import Organization, Point, Request, Trip, Visitor


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "kind")
    list_filter = ("kind",)
    search_fields = ("name",)


class VisitorInline(admin.TabularInline):
    model = Visitor
    extra = 0
    fields = ("full_name", "organization", "arrival_date", "departure_date", "status", "camp_status", "needs_camp", "needs_meals")
    readonly_fields = fields
    can_delete = False


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ("number", "purpose", "author", "created_at", "is_cancelled")
    list_filter = ("is_cancelled",)
    search_fields = ("purpose", "contact_name", "visitors__full_name")
    inlines = [VisitorInline]


@admin.register(Visitor)
class VisitorAdmin(admin.ModelAdmin):
    list_display = ("full_name", "organization", "arrival_date", "departure_date", "status", "camp_status")
    list_filter = ("status", "camp_status", "needs_camp", "needs_meals", "arrival_date")
    search_fields = ("full_name", "vehicle_plate")
    raw_id_fields = ("request",)


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ("date", "direction", "vehicle_plate", "vehicle_make", "driver_name", "entered_at", "exited_at")
    list_filter = ("direction", "date")
    search_fields = ("vehicle_plate", "driver_name")


@admin.register(Point)
class PointAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "is_guarded", "is_active", "order")
    list_editable = ("address", "is_guarded", "is_active", "order")
