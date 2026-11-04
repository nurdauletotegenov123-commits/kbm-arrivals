from django.contrib import admin

from .models import Delivery, DeliveryItem


class ItemInline(admin.TabularInline):
    model = DeliveryItem
    extra = 0


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ["truck_plate", "trailer_plate", "date_from", "date_to", "driver_name", "status", "source"]
    list_filter = ["status", "source", "date_from"]
    search_fields = ["truck_plate", "trailer_plate", "plate_raw", "driver_name", "items__supplier", "items__contract"]
    readonly_fields = ["entered_at", "entered_by", "exited_at", "exited_by", "created_by", "created_at", "updated_at"]
    inlines = [ItemInline]
