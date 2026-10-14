from django.contrib import admin
from django.db.models import Count, Max, Q

from apps.suppliers.models import Supplier, SupplierFeed


class SupplierFeedInline(admin.TabularInline):
    model = SupplierFeed
    extra = 0
    fields = ["name", "kind", "url", "file", "adapter_code", "is_active"]


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
    inlines = [SupplierFeedInline]

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
    list_display = ["supplier", "name", "kind", "adapter_code", "is_active", "last_success_at"]
    list_filter = ["kind", "is_active"]
    search_fields = ["name", "supplier__code", "url"]
