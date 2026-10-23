from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Checkpoint, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "full_name", "role", "checkpoint", "is_active")
    list_filter = ("role", "is_active", "checkpoint")
    search_fields = ("username", "full_name", "email")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Роль и служба", {"fields": ("full_name", "role", "checkpoint", "phone", "notify_by_email")}),
    )


admin.site.register(Checkpoint)
