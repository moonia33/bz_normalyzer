from django.contrib import admin

from apps.catalog_export.models import ExportRun


@admin.register(ExportRun)
class ExportRunAdmin(admin.ModelAdmin):
    list_display = ["id", "kind", "status", "item_count", "file_name", "created_at"]
    list_filter = ["kind", "status"]
    readonly_fields = ["kind", "status", "file_name", "file_sha256", "item_count",
                       "options", "error_message", "shop_response", "triggered_by"]
