from django.contrib import admin

from apps.catalog_export.models import ExportRun, PipelineRun


@admin.register(ExportRun)
class ExportRunAdmin(admin.ModelAdmin):
    list_display = ["id", "kind", "status", "item_count", "file_name", "created_at"]
    list_filter = ["kind", "status"]
    readonly_fields = ["kind", "status", "file_name", "file_sha256", "item_count",
                       "options", "error_message", "shop_response", "triggered_by"]


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    list_display = ["id", "supplier", "stage", "status", "dry_run", "created_at", "finished_at"]
    list_filter = ["stage", "status", "supplier"]
    readonly_fields = [
        "supplier", "stage", "status", "dry_run", "details", "error_message", "finished_at",
    ]
