from django.contrib import admin
from django.db.models import Count, Max, Q

from apps.suppliers.models import Supplier, SupplierFeed, SupplierReferenceItem, SupplierSchedule


class SupplierFeedInline(admin.TabularInline):
    model = SupplierFeed
    extra = 0
    fields = [
        "name", "kind", "role", "language", "feed_group",
        "url", "file", "adapter_code", "sync_interval_minutes", "is_active",
    ]


class SupplierScheduleInline(admin.StackedInline):
    model = SupplierSchedule
    extra = 0
    max_num = 1
    can_delete = False
    fields = [
        "enabled",
        ("catalog_weekday", "catalog_hour"),
        ("price_interval_hours", "price_change_guard_pct"),
        "stock_interval_minutes",
        ("last_catalog_at", "last_prices_at", "last_stock_at"),
        ("catalog_phase", "translation_batch_id"),
    ]
    readonly_fields = [
        "last_catalog_at",
        "last_prices_at",
        "last_stock_at",
        "catalog_phase",
        "translation_batch_id",
    ]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    search_fields = ["code", "name"]
    list_display = [
        "code",
        "name",
        "is_active",
        "feed_count",
        "offer_count",
        "published_offer_count",
        "last_feed_success_at",
        "updated_at",
    ]
    list_filter = ["is_active"]
    inlines = [SupplierScheduleInline, SupplierFeedInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            feed_count=Count("feeds", distinct=True),
            offer_count=Count("offers", distinct=True),
            published_offer_count=Count(
                "offers",
                filter=Q(offers__is_published=True),
                distinct=True,
            ),
            last_feed_success_at=Max("feeds__last_success_at"),
        )

    @admin.display(description="feeds")
    def feed_count(self, obj):
        return obj.feed_count

    @admin.display(description="offers")
    def offer_count(self, obj):
        return obj.offer_count

    @admin.display(description="published")
    def published_offer_count(self, obj):
        return obj.published_offer_count

    @admin.display(description="last success")
    def last_feed_success_at(self, obj):
        return obj.last_feed_success_at


@admin.register(SupplierFeed)
class SupplierFeedAdmin(admin.ModelAdmin):
    list_display = [
        "supplier", "name", "kind", "role", "language", "feed_group",
        "sync_interval_minutes", "is_active", "last_success_at",
    ]
    list_filter = ["kind", "role", "language", "is_active"]
    search_fields = ["name", "supplier__code", "url"]


@admin.register(SupplierReferenceItem)
class SupplierReferenceItemAdmin(admin.ModelAdmin):
    list_display = ["supplier", "code", "name", "ean", "purchase_price", "rrp_price", "trade_pack_qty", "source"]
    list_filter = ["supplier", "source"]
    search_fields = ["code", "name", "ean"]
