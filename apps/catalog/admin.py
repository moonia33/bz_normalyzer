from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.ingestion.models import Offer
from apps.catalog.models import (
    Brand,
    Category,
    Product,
    ProductText,
    UnmappedAttribute,
)


def _effective_image_urls(obj):
    urls: list[str] = []
    main_image = getattr(obj, "main_image_url", "") or ""
    main_image = str(main_image).strip()
    if main_image:
        urls.append(main_image)

    images = getattr(obj, "images", []) or []
    if isinstance(images, list):
        for item in images:
            value = str(item).strip()
            if value and value not in urls:
                urls.append(value)
    return [url for url in urls if url]


def _image_count_for(obj):
    return len(_effective_image_urls(obj))


class HasImageFilter(admin.SimpleListFilter):
    title = "nuotraukos"
    parameter_name = "has_image"

    def lookups(self, request, model_admin):
        return [
            ("yes", "Yra nuotrauka"),
            ("no", "Nėra nuotraukos"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value == "yes":
            ids = [obj.pk for obj in queryset if _image_count_for(obj) > 0]
            return queryset.filter(pk__in=ids)
        if value == "no":
            ids = [obj.pk for obj in queryset if _image_count_for(obj) == 0]
            return queryset.filter(pk__in=ids)
        return queryset


class HasImageCountFilter(admin.SimpleListFilter):
    title = "nuotraukų kiekis"
    parameter_name = "image_count"

    def lookups(self, request, model_admin):
        return [
            ("0", "0"),
            ("1", "1"),
            ("2_plus", "2+"),
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset

        if value == "0":
            ids = [obj.pk for obj in queryset if _image_count_for(obj) == 0]
        elif value == "1":
            ids = [obj.pk for obj in queryset if _image_count_for(obj) == 1]
        elif value == "2_plus":
            ids = [obj.pk for obj in queryset if _image_count_for(obj) >= 2]
        else:
            return queryset
        return queryset.filter(pk__in=ids)


class SupplierFilter(admin.SimpleListFilter):
    title = "suplier"
    parameter_name = "supplier_code"

    def lookups(self, request, model_admin):
        supplier_codes = (
            Offer.objects.exclude(supplier__code="")
            .values_list("supplier__code", flat=True)
            .distinct()
            .order_by("supplier__code")
        )
        return [(code, code) for code in supplier_codes]

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(offers__supplier__code=value).distinct()


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    search_fields = ["name", "slug"]
    list_display = ["name", "slug", "updated_at"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ["name", "slug"]
    list_display = ["name", "parent", "is_active"]
    list_filter = ["is_active"]
    prepopulated_fields = {"slug": ("name",)}


class ProductTextInline(admin.TabularInline):
    model = ProductText
    extra = 0
    fields = ["language", "name", "slug", "description"]


@admin.register(ProductText)
class ProductTextAdmin(admin.ModelAdmin):
    list_display = [
        "product",
        "language",
        "name",
        "slug",
        "seo_title",
        "has_description",
        "updated_at",
    ]
    list_filter = ["language"]
    list_select_related = ["product", "language"]
    list_per_page = 50
    raw_id_fields = ["product", "source_offer"]
    search_fields = ["product__sku", "product__name", "name"]
    readonly_fields = ["created_at", "updated_at"]

    @admin.display(description="Turi aprašymą", boolean=True)
    def has_description(self, obj):
        return bool(obj.description)


@admin.register(UnmappedAttribute)
class UnmappedAttributeAdmin(admin.ModelAdmin):
    list_display = [
        "code",
        "kind",
        "supplier",
        "category_raw",
        "count",
        "last_seen",
    ]
    list_filter = ["kind", "supplier"]
    search_fields = ["code", "label"]
    ordering = ["-count"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    show_facets = admin.ShowFacets.NEVER
    list_per_page = 50
    list_select_related = [
        "category",
        "category__parent__parent__parent",
        "brand",
    ]
    inlines = [ProductTextInline]
    search_fields = ["sku", "name", "ean"]
    list_display = [
        "thumbnail",
        "image_count",
        "sku",
        "source_external_id",
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
        "supplier",
        "brand",
        "store_category",
        "supplier_category",
        "supplier_sub_category",
        "family_raw",
        "purchase_price",
        "rrp_price",
        "is_active",
    ]
    list_filter = [
        "is_active",
        "brand",
        "category_raw",
        HasImageFilter,
        SupplierFilter,
    ]
    readonly_fields = [
        "thumbnail",
        "main_image_url",
        "images",
        "supplier_category",
        "supplier_sub_category",
        "default_store_category",
    ]
    actions = ["deactivate_without_image", "activate_with_image"]

    @admin.display(description="Image")
    def thumbnail(self, obj):
        change_url = reverse("admin:catalog_product_change", args=[obj.pk])
        image_url = _effective_image_urls(obj)[0] if _effective_image_urls(obj) else ""
        if not image_url:
            return format_html('<a href="{0}" title="Redaguoti prekę">{1}</a>', change_url, "-")
        return format_html(
            '<a href="{0}" title="Redaguoti prekę">'
            '<img src="{1}" alt="{2}" style="height:40px;width:40px;object-fit:cover;border-radius:4px;display:block;" />'
            "</a>",
            change_url,
            image_url,
            obj.sku,
        )

    @admin.display(description="Images")
    def image_count(self, obj):
        return _image_count_for(obj)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("offers__supplier")

    @admin.display(description="Suplier category")
    def supplier_category(self, obj):
        if obj.category_raw:
            return obj.category_raw
        for offer in obj.offers.all():
            if offer.category_raw:
                return offer.category_raw
        return "-"

    @admin.display(description="Suplier sub category")
    def supplier_sub_category(self, obj):
        if obj.sub_category_raw:
            return obj.sub_category_raw
        for offer in obj.offers.all():
            if offer.sub_category_raw:
                return offer.sub_category_raw
        return "-"

    @admin.display(description="Default store category")
    def default_store_category(self, obj):
        return obj.category.path() if obj.category_id else "-"

    @admin.display(description="Store category")
    def store_category(self, obj):
        return obj.category.path() if obj.category_id else "-"

    @admin.display(description="Suplier")
    def supplier(self, obj):
        for offer in obj.offers.all():
            if offer.supplier_id and offer.supplier.code:
                return offer.supplier.code
        return "-"

    @admin.action(description="Išjungti be nuotraukų")
    def deactivate_without_image(self, request, queryset):
        ids = [obj.pk for obj in queryset if _image_count_for(obj) == 0]
        queryset.filter(pk__in=ids).update(is_active=False)
        self.message_user(request, f"Išjungta prekių be nuotraukų: {len(ids)}")

    @admin.action(description="Aktyvuoti su nuotraukomis")
    def activate_with_image(self, request, queryset):
        ids = [obj.pk for obj in queryset if _image_count_for(obj) > 0]
        queryset.filter(pk__in=ids).update(is_active=True)
        self.message_user(request, f"Aktyvuota prekių su nuotraukomis: {len(ids)}")
