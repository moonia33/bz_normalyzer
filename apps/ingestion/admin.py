from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.utils import timezone

from apps.ingestion.models import ImportJob, Offer, OfferText, RawItem
from apps.ingestion.pipeline import run_import


@admin.register(ImportJob)
class ImportJobAdmin(admin.ModelAdmin):
    list_display = ["id", "supplier", "feed", "status", "stats_summary", "started_at", "finished_at"]
    list_filter = ["status", "supplier"]
    actions = ["run_selected"]
    readonly_fields = ["status", "started_at", "finished_at", "stats", "error_message"]

    @admin.display(description="stats")
    def stats_summary(self, obj):
        stats = obj.stats or {}
        return (
            f"fetched={stats.get('fetched', 0)} | "
            f"norm={stats.get('normalized', 0)} | "
            f"saved={stats.get('upserted', 0)} | "
            f"errors={stats.get('errors', 0)}"
        )

    @admin.action(description="Paleisti pasirinktas importo užduotis")
    def run_selected(self, request, queryset):
        for job in queryset:
            run_import(job)
        self.message_user(request, f"Įvykdyta: {queryset.count()}")


class OfferTextInline(admin.TabularInline):
    model = OfferText
    extra = 0
    fields = ["language", "name", "base_name"]
    readonly_fields = ["updated_at"]


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    inlines = [OfferTextInline]
    list_display = [
        "supplier",
        "art_no",
        "product_group",
        "name",
        "base_name",
        "size_raw",
        "package_raw",
        "unit_weight_raw",
        "shipping_height_cm",
        "shipping_width_cm",
        "shipping_depth_cm",
        "shipping_weight_kg",
        "brand_name_raw",
        "purchase_price",
        "rrp_price",
        "selling_price",
        "stock",
        "availability_date",
        "feed_status",
        "store_category",
        "category_raw",
        "sub_category_raw",
        "family_raw",
        "image_count",
        "main_image",
        "is_available",
        "is_discontinued",
        "is_published",
    ]
    list_filter = [
        "supplier",
        "is_available",
        "is_discontinued",
        "is_published",
        "category",
        "brand",
        "sub_category_raw",
        "family_raw",
        "product_group",
    ]
    search_fields = ["name", "external_id", "ean", "sku"]
    actions = ["publish", "unpublish", "reprice", "deactivate_without_main_image"]
    readonly_fields = ["main_image", "product_group", "art_no"]

    @admin.display(description="Art-No.")
    def art_no(self, obj):
        return obj.external_id

    @admin.display(description="Product Group")
    def product_group(self, obj):
        if obj.product_group:
            return obj.product_group
        identifiers = obj.extra.get("identifiers") if isinstance(obj.extra, dict) else {}
        if isinstance(identifiers, dict):
            return identifiers.get("product_group", "")
        return ""

    @admin.display(description="Images")
    def image_count(self, obj):
        return len(obj.images) if isinstance(obj.images, list) else 0

    @admin.display(description="Feed status")
    def feed_status(self, obj):
        extra = obj.extra if isinstance(obj.extra, dict) else {}
        source = str(extra.get("source", "")).strip()
        status = str(extra.get("status", "")).strip()
        if status and source:
            return f"{status} ({source})"
        if status:
            return status
        if source:
            return source
        return "-"

    @admin.display(description="Store category")
    def store_category(self, obj):
        return obj.category.path() if obj.category_id else "-"

    @admin.display(description="Main image")
    def main_image(self, obj):
        url = (obj.main_image_url or "").strip()
        if not url and isinstance(obj.images, list) and obj.images:
            url = str(obj.images[0]).strip()
        if not url:
            return "-"
        change_url = reverse("admin:catalog_product_change", args=[obj.product_id]) if obj.product_id else None
        if change_url:
            return format_html(
                '<a href="{0}" title="Redaguoti prekę">'
                '<img src="{1}" alt="{2}" style="height:40px;width:40px;object-fit:cover;border-radius:4px;" />'
                "</a>",
                change_url,
                url,
                obj.external_id,
            )
        return format_html(
            '<a href="{0}" target="_blank" rel="noopener noreferrer">'
            '<img src="{0}" alt="{1}" style="height:40px;width:40px;object-fit:cover;border-radius:4px;" />'
            "</a>",
            url,
            obj.external_id,
        )

    @admin.action(description="Publikuoti API")
    def publish(self, request, queryset):
        queryset.update(is_published=True, updated_at=timezone.now())

    @admin.action(description="Išimti iš API")
    def unpublish(self, request, queryset):
        queryset.update(is_published=False, updated_at=timezone.now())

    @admin.action(description="Išjungti pasiūlymus be nuotraukų")
    def deactivate_without_main_image(self, request, queryset):
        updated = queryset.filter(main_image_url="", images=[]).update(is_available=False, is_published=False)
        self.message_user(request, f"Išjungta pasiūlymų be nuotraukų: {updated}")

    @admin.action(description="Perskaičiuoti pardavimo kainas")
    def reprice(self, request, queryset):
        from apps.pricing.engine import apply_pricing

        count = apply_pricing(queryset)
        self.message_user(request, f"Atnaujinta kainų: {count}")


@admin.register(RawItem)
class RawItemAdmin(admin.ModelAdmin):
    list_display = ["supplier", "external_id", "job", "created_at"]
    list_filter = ["supplier"]
    search_fields = ["external_id"]
    readonly_fields = ["job", "supplier", "external_id", "payload", "created_at"]
