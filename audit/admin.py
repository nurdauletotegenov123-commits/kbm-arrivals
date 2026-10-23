from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "username", "action", "object_type", "object_repr", "ip_address")
    list_filter = ("action", "object_type")
    search_fields = ("username", "object_repr", "object_id")

    # Журнал нельзя менять даже суперпользователю.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
