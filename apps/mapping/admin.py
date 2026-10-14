from django.contrib import admin
from django.db.models import Q

from apps.ingestion.models import Offer
from apps.mapping.models import BrandMapping, CategoryMapping, ProductLink


@admin.register(CategoryMapping)
class CategoryMappingAdmin(admin.ModelAdmin):
    list_display = ["supplier_category_path", "category"]
    list_filter = ["supplier"]
    search_fields = ["source_path"]
    autocomplete_fields = ["category", "supplier"]
    fields = ["supplier", "source_path", "category"]

    @admin.display(description="Suplier category > subcategory")
    def supplier_category_path(self, obj):
        return obj.source_path

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not obj.category_id:
            return

        source_path = obj.source_path
        raw_category = (obj.source_category_raw or "").strip()
        raw_sub_category = (obj.source_sub_category_raw or "").strip()

        match_filter = Q(category_raw__iexact=source_path)
        if raw_sub_category:
            match_filter |= Q(
                category_raw__iexact=raw_category,
                sub_category_raw__iexact=raw_sub_category,
            )
        else:
            match_filter |= Q(category_raw__iexact=raw_category)

        offers = Offer.objects.filter(supplier=obj.supplier).filter(match_filter)
        offers.update(category=obj.category)
        product_ids = list(offers.exclude(product_id__isnull=True).values_list("product_id", flat=True))
        if product_ids:
            from apps.catalog.models import Product

            Product.objects.filter(pk__in=product_ids).update(category=obj.category)


@admin.register(BrandMapping)
class BrandMappingAdmin(admin.ModelAdmin):
    list_display = ["supplier", "source_name", "brand"]
    list_filter = ["supplier"]
    search_fields = ["source_name"]
    autocomplete_fields = ["brand", "supplier"]


@admin.register(ProductLink)
class ProductLinkAdmin(admin.ModelAdmin):
    list_display = ["supplier", "external_id", "product"]
    list_filter = ["supplier"]
    search_fields = ["external_id"]
    autocomplete_fields = ["product", "supplier"]
