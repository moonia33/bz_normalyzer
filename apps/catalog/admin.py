from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.ingestion.models import Offer
from apps.catalog.models import Brand, Category, Product


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


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    show_facets = admin.ShowFacets.NEVER
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

    @admin.display(description="Suplier category")
    def supplier_category(self, obj):
        if obj.category_raw:
            return obj.category_raw
        value = obj.offers.exclude(category_raw="").values_list("category_raw", flat=True).first()
        return value or "-"

    @admin.display(description="Suplier sub category")
    def supplier_sub_category(self, obj):
        if obj.sub_category_raw:
            return obj.sub_category_raw
        value = obj.offers.exclude(sub_category_raw="").values_list("sub_category_raw", flat=True).first()
        return value or "-"

    @admin.display(description="Default store category")
    def default_store_category(self, obj):
        return obj.category.path() if obj.category_id else "-"

    @admin.display(description="Store category")
    def store_category(self, obj):
        return obj.category.path() if obj.category_id else "-"

    @admin.display(description="Suplier")
    def supplier(self, obj):
        code = obj.offers.exclude(supplier__code="").values_list("supplier__code", flat=True).first()
        return code or "-"

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
