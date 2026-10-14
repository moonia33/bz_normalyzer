from __future__ import annotations

from django.utils.text import slugify

from apps.catalog.models import Brand, Category
from apps.mapping.models import BrandMapping, CategoryMapping, ProductLink
from apps.suppliers.models import Supplier


def _normalize_supplier_category_values(
    source_category_raw: str,
    source_sub_category_raw: str,
) -> tuple[str, str]:
    category = (source_category_raw or "").strip()
    sub_category = (source_sub_category_raw or "").strip()
    if category and not sub_category and ">" in category:
        parts = [part.strip() for part in category.split(">") if part.strip()]
        if parts:
            category = parts[0]
            sub_category = " > ".join(parts[1:])
    return category, sub_category


def map_category(
    supplier: Supplier,
    source_category_raw: str,
    source_sub_category_raw: str = "",
) -> Category | None:
    category_raw, sub_category_raw = _normalize_supplier_category_values(
        source_category_raw,
        source_sub_category_raw,
    )
    if not category_raw and not sub_category_raw:
        return None

    source_path = CategoryMapping.build_source_path(category_raw, sub_category_raw)

    mapping = (
        CategoryMapping.objects.filter(supplier=supplier, source_path__iexact=source_path)
        .select_related("category")
        .first()
    )
    if mapping is None:
        mapping = CategoryMapping.objects.create(
            supplier=supplier,
            source_category_raw=category_raw,
            source_sub_category_raw=sub_category_raw,
            source_path=source_path,
        )
    if mapping.category_id:
        return mapping.category

    if sub_category_raw:
        parent_mapping = (
            CategoryMapping.objects.filter(
                supplier=supplier,
                source_category_raw__iexact=category_raw,
                source_sub_category_raw="",
            )
            .select_related("category")
            .first()
        )
        if parent_mapping:
            if parent_mapping.category_id:
                return parent_mapping.category
    return None


def map_brand(supplier: Supplier, source_name: str) -> Brand | None:
    if not source_name:
        return None
    normalized_name = source_name.strip()
    if not normalized_name:
        return None
    mapping = (
        BrandMapping.objects.filter(supplier=supplier, source_name=normalized_name)
        .select_related("brand")
        .first()
    )
    if mapping:
        return mapping.brand
    existing_brand = Brand.objects.filter(name__iexact=normalized_name).first()
    if existing_brand:
        BrandMapping.objects.get_or_create(
            supplier=supplier,
            source_name=normalized_name,
            defaults={"brand": existing_brand},
        )
        return existing_brand
    new_brand = Brand.objects.create(name=normalized_name, slug=slugify(normalized_name))
    BrandMapping.objects.get_or_create(
        supplier=supplier,
        source_name=normalized_name,
        defaults={"brand": new_brand},
    )
    return new_brand


def linked_product(supplier: Supplier, external_id: str):
    link = (
        ProductLink.objects.filter(supplier=supplier, external_id=external_id)
        .select_related("product")
        .first()
    )
    return link.product if link else None
