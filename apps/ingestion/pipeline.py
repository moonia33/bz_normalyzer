from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.catalog.models import Product
from apps.ingestion.dto import NormalizedItem
from apps.ingestion.models import ImportJob, Offer, RawItem
from apps.mapping.models import ProductLink
from apps.mapping.services import linked_product, map_brand, map_category
from apps.pricing.engine import compute_selling_price
from apps.suppliers.adapters.base import AdapterError
from apps.suppliers.adapters.resolver import resolve_adapter
from apps.suppliers.models import SupplierFeed


def run_import(job: ImportJob) -> ImportJob:
    job.status = ImportJob.Status.RUNNING
    job.started_at = timezone.now()
    job.error_message = ""
    job.save(update_fields=["status", "started_at", "error_message", "updated_at"])

    stats = {
        "fetched": 0,
        "normalized": 0,
        "upserted": 0,
        "skipped_partial_missing": 0,
        "unmapped_categories": 0,
        "errors": 0,
    }
    try:
        feed = _resolve_feed(job)
        adapter = resolve_adapter(feed)
        uploaded = job.uploaded_file.path if job.uploaded_file else None
        payload = adapter.fetch(uploaded_path=uploaded)
        stats["fetched"] = 1

        for item in adapter.iter_items(payload):
            stats["normalized"] += 1
            try:
                with transaction.atomic():
                    _store_raw(job, item)
                    offer = _upsert_offer(job, item, stats)
                    if offer is not None:
                        stats["upserted"] += 1
            except Exception:
                stats["errors"] += 1

        feed.last_success_at = timezone.now()
        feed.save(update_fields=["last_success_at", "updated_at"])
        job.stats = stats
        job.status = ImportJob.Status.SUCCESS
        job.finished_at = timezone.now()
        job.save(update_fields=["stats", "status", "finished_at", "updated_at"])
    except Exception as exc:
        job.stats = stats
        job.status = ImportJob.Status.FAILED
        job.error_message = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["stats", "status", "error_message", "finished_at", "updated_at"])
        raise
    return job


def _resolve_feed(job: ImportJob) -> SupplierFeed:
    if job.feed_id:
        return job.feed
    feed = job.supplier.feeds.filter(is_active=True).first()
    if not feed:
        raise AdapterError(f"Tiekėjas {job.supplier.code} neturi aktyvaus šaltinio (SupplierFeed).")
    job.feed = feed
    job.save(update_fields=["feed"])
    return feed


def _store_raw(job: ImportJob, item: NormalizedItem) -> None:
    RawItem.objects.create(
        job=job,
        supplier=job.supplier,
        external_id=item.external_id,
        payload=item.raw,
    )


def _find_matching_product(job: ImportJob, item: NormalizedItem, offer: Offer) -> Product | None:
    existing = linked_product(job.supplier, item.external_id)
    if existing:
        return existing

    candidate_filters: list[Q] = []

    if offer.product_group and offer.base_name:
        candidate_filters.append(
            Q(product_group__iexact=offer.product_group)
            & Q(base_name__iexact=offer.base_name)
        )
        if offer.category_id:
            candidate_filters.append(
                Q(product_group__iexact=offer.product_group)
                & Q(base_name__iexact=offer.base_name)
                & Q(category_id=offer.category_id)
            )
        if offer.sub_category_raw:
            candidate_filters.append(
                Q(product_group__iexact=offer.product_group)
                & Q(base_name__iexact=offer.base_name)
                & Q(sub_category_raw__iexact=offer.sub_category_raw)
            )
        if offer.family_raw:
            candidate_filters.append(
                Q(product_group__iexact=offer.product_group)
                & Q(base_name__iexact=offer.base_name)
                & Q(family_raw__iexact=offer.family_raw)
            )

    if offer.base_name and offer.sub_category_raw and offer.family_raw:
        candidate_filters.append(
            Q(base_name__iexact=offer.base_name)
            & Q(sub_category_raw__iexact=offer.sub_category_raw)
            & Q(family_raw__iexact=offer.family_raw)
        )

    if offer.base_name and offer.sub_category_raw:
        candidate_filters.append(
            Q(base_name__iexact=offer.base_name)
            & Q(sub_category_raw__iexact=offer.sub_category_raw)
        )

    if offer.base_name and offer.category_id:
        candidate_filters.append(Q(base_name__iexact=offer.base_name) & Q(category_id=offer.category_id))

    if offer.brand_id and offer.base_name:
        candidate_filters.append(Q(brand_id=offer.brand_id) & Q(base_name__iexact=offer.base_name))

    if offer.product_group and not offer.sub_category_raw and not offer.family_raw:
        candidate_filters.append(Q(product_group__iexact=offer.product_group))

    for candidate in candidate_filters:
        product = Product.objects.filter(candidate).order_by("pk").first()
        if product:
            return product

    filters = Q()
    if offer.product_group:
        filters &= Q(product_group__iexact=offer.product_group)
    if offer.sub_category_raw:
        filters &= Q(sub_category_raw__iexact=offer.sub_category_raw)
    if offer.family_raw:
        filters &= Q(family_raw__iexact=offer.family_raw)
    if offer.brand_id:
        filters &= Q(brand_id=offer.brand_id)
    if offer.category_id:
        filters &= Q(category_id=offer.category_id)
    if offer.base_name:
        filters &= Q(base_name__iexact=offer.base_name)

    if filters:
        product = Product.objects.filter(filters).order_by("pk").first()
        if product:
            return product

    return None


def _ensure_product(job: ImportJob, item: NormalizedItem, offer: Offer) -> Product:
    product = _find_matching_product(job, item, offer) or offer.product
    if not product:
        sku = f"{job.supplier.code}-{item.external_id}"[:64]
        product, _ = Product.objects.get_or_create(
            sku=sku,
            defaults={
                "name": item.name,
                "ean": item.ean,
                "source_external_id": item.external_id,
                "description": item.description,
                "attributes": item.attributes,
                "brand": offer.brand,
                "category": offer.category,
                "category_raw": offer.category_raw,
                "size_raw": offer.size_raw,
                "package_raw": offer.package_raw,
                "unit_weight_raw": offer.unit_weight_raw,
                "sub_category_raw": offer.sub_category_raw,
                "shipping_height_cm": offer.shipping_height_cm,
                "shipping_width_cm": offer.shipping_width_cm,
                "shipping_depth_cm": offer.shipping_depth_cm,
                "shipping_weight_kg": offer.shipping_weight_kg,
                "main_image_url": offer.main_image_url,
                "images": list(offer.images) if isinstance(offer.images, list) else [],
            },
        )

    if item.external_id and (not product.source_external_id or product.source_external_id == item.external_id):
        product.source_external_id = item.external_id

    changed_fields: list[str] = []
    if item.name and (not product.name or product.name == product.base_name or product.name == item.name):
        product.name = item.name
        changed_fields.append("name")
    if item.ean and product.ean != item.ean:
        product.ean = item.ean
        changed_fields.append("ean")
    if item.description and product.description != item.description:
        product.description = item.description
        changed_fields.append("description")
    if offer.purchase_price is not None and product.purchase_price != offer.purchase_price:
        product.purchase_price = offer.purchase_price
        changed_fields.append("purchase_price")
    if offer.rrp_price is not None and product.rrp_price != offer.rrp_price:
        product.rrp_price = offer.rrp_price
        changed_fields.append("rrp_price")
    if offer.base_name and product.base_name != offer.base_name:
        product.base_name = offer.base_name
        changed_fields.append("base_name")
    if offer.product_group and product.product_group != offer.product_group:
        product.product_group = offer.product_group
        changed_fields.append("product_group")
    if offer.sub_category_raw and product.sub_category_raw != offer.sub_category_raw:
        product.sub_category_raw = offer.sub_category_raw
        changed_fields.append("sub_category_raw")
    if offer.size_raw and not product.size_raw:
        product.size_raw = offer.size_raw
        changed_fields.append("size_raw")
    if offer.package_raw and not product.package_raw:
        product.package_raw = offer.package_raw
        changed_fields.append("package_raw")
    if offer.unit_weight_raw and not product.unit_weight_raw:
        product.unit_weight_raw = offer.unit_weight_raw
        changed_fields.append("unit_weight_raw")
    if offer.shipping_height_cm is not None and product.shipping_height_cm is None:
        product.shipping_height_cm = offer.shipping_height_cm
        changed_fields.append("shipping_height_cm")
    if offer.shipping_width_cm is not None and product.shipping_width_cm is None:
        product.shipping_width_cm = offer.shipping_width_cm
        changed_fields.append("shipping_width_cm")
    if offer.shipping_depth_cm is not None and product.shipping_depth_cm is None:
        product.shipping_depth_cm = offer.shipping_depth_cm
        changed_fields.append("shipping_depth_cm")
    if offer.shipping_weight_kg is not None and product.shipping_weight_kg is None:
        product.shipping_weight_kg = offer.shipping_weight_kg
        changed_fields.append("shipping_weight_kg")
    if offer.category_raw and product.category_raw != offer.category_raw:
        product.category_raw = offer.category_raw
        changed_fields.append("category_raw")
    if offer.family_raw and product.family_raw != offer.family_raw:
        product.family_raw = offer.family_raw
        changed_fields.append("family_raw")
    if offer.main_image_url and product.main_image_url != offer.main_image_url:
        product.main_image_url = offer.main_image_url
        changed_fields.append("main_image_url")
    if isinstance(offer.images, list):
        normalized_images = [str(url).strip() for url in offer.images if str(url).strip()]
        if product.images != normalized_images:
            product.images = normalized_images
            changed_fields.append("images")
    elif product.images:
        product.images = []
        changed_fields.append("images")
    if offer.technical_description and product.technical_description != offer.technical_description:
        product.technical_description = offer.technical_description
        changed_fields.append("technical_description")
    if offer.combined_full_text and product.combined_full_text != offer.combined_full_text:
        product.combined_full_text = offer.combined_full_text
        changed_fields.append("combined_full_text")
    if item.attributes and product.attributes != item.attributes:
        product.attributes = item.attributes
        changed_fields.append("attributes")
    if offer.brand_id and product.brand_id != offer.brand_id:
        product.brand = offer.brand
        changed_fields.append("brand")
    if offer.category_id and product.category_id != offer.category_id:
        product.category = offer.category
        changed_fields.append("category")
    if changed_fields:
        product.save(update_fields=[*changed_fields, "updated_at"])

    ProductLink.objects.get_or_create(
        supplier=job.supplier,
        external_id=item.external_id,
        defaults={"product": product},
    )
    return product


def _upsert_offer(job: ImportJob, item: NormalizedItem, stats: dict) -> Offer | None:
    partial_update = _is_partial_update(item)
    offer = Offer.objects.filter(supplier=job.supplier, external_id=item.external_id).first()
    if offer is None and partial_update:
        offer = _find_partial_offer_by_secondary_identity(job, item)
    if offer is None and partial_update:
        stats["skipped_partial_missing"] = int(stats.get("skipped_partial_missing", 0)) + 1
        return None
    if offer is None:
        offer = Offer.objects.create(
            supplier=job.supplier,
            external_id=item.external_id,
            name=item.name,
        )
        created = True
    else:
        created = False

    is_discontinued = _is_discontinued_item(item)

    incoming_sub_category = _extra_text(item.extra, "sub_category_raw")
    incoming_size = _extra_text(item.extra, "size_raw")
    incoming_package = _extra_text(item.extra, "package_raw")
    incoming_unit_weight = _extra_text(item.extra, "unit_weight_raw")
    incoming_shipping_height_cm = _extra_decimal(item.extra, "shipping_height_cm")
    incoming_shipping_width_cm = _extra_decimal(item.extra, "shipping_width_cm")
    incoming_shipping_depth_cm = _extra_decimal(item.extra, "shipping_depth_cm")
    incoming_shipping_weight_kg = _extra_decimal(item.extra, "shipping_weight_kg")
    category_raw = (item.category_path or "").strip()
    sub_category_raw = incoming_sub_category.strip()

    if partial_update and not created:
        category = offer.category
    else:
        category = map_category(job.supplier, category_raw, sub_category_raw)
        if (category_raw or sub_category_raw) and not category:
            stats["unmapped_categories"] += 1

    if partial_update and not item.brand_name and not created:
        brand = offer.brand
    else:
        brand = map_brand(job.supplier, item.brand_name)

    if partial_update and not created:
        if item.ean and not offer.ean:
            offer.ean = item.ean
    elif item.ean or not partial_update or created:
        offer.ean = item.ean
    if not partial_update:
        offer.sku = item.sku or offer.sku or item.external_id

    incoming_product_group = _extra_text(item.extra, "product_group")
    if incoming_product_group or not partial_update or created:
        offer.product_group = incoming_product_group

    if (not partial_update) or created or (item.name and item.name != item.external_id):
        offer.name = item.name
    if item.brand_name or not partial_update or created:
        offer.brand_name_raw = item.brand_name
    offer.brand = brand
    if item.category_path or not partial_update or created:
        offer.category_raw = item.category_path
    if incoming_sub_category or not partial_update or created:
        offer.sub_category_raw = incoming_sub_category
    if incoming_size or not partial_update or created:
        offer.size_raw = incoming_size
    if incoming_package or not partial_update or created:
        offer.package_raw = incoming_package
    if incoming_unit_weight or not partial_update or created:
        offer.unit_weight_raw = incoming_unit_weight
    if incoming_shipping_height_cm is not None or not partial_update or created:
        offer.shipping_height_cm = incoming_shipping_height_cm
    if incoming_shipping_width_cm is not None or not partial_update or created:
        offer.shipping_width_cm = incoming_shipping_width_cm
    if incoming_shipping_depth_cm is not None or not partial_update or created:
        offer.shipping_depth_cm = incoming_shipping_depth_cm
    if incoming_shipping_weight_kg is not None or not partial_update or created:
        offer.shipping_weight_kg = incoming_shipping_weight_kg
    incoming_family = _extra_text(item.extra, "family_raw")
    if incoming_family or not partial_update or created:
        offer.family_raw = incoming_family
    offer.category = category

    if item.description or not partial_update or created:
        offer.description = item.description
    incoming_base_name = _extra_text(item.extra, "base_name")
    if incoming_base_name or not partial_update or created:
        offer.base_name = incoming_base_name
    incoming_technical_description = _extra_text(item.extra, "technical_description")
    if incoming_technical_description or not partial_update or created:
        offer.technical_description = incoming_technical_description
    incoming_combined_full_text = _extra_text(item.extra, "combined_full_text")
    if incoming_combined_full_text or not partial_update or created:
        offer.combined_full_text = incoming_combined_full_text

    incoming_main_image_url = _extra_text(item.extra, "main_image_url")
    if incoming_main_image_url or not partial_update or created:
        offer.main_image_url = incoming_main_image_url
    if item.attributes or not partial_update or created:
        offer.attributes = item.attributes
    if item.images or not partial_update or created:
        offer.images = item.images

    offer.stock = item.stock
    offer.availability_date = item.availability_date
    if item.purchase_price is not None or not partial_update or created:
        offer.purchase_price = item.purchase_price
    incoming_rrp_price = _extra_decimal(item.extra, "rrp_price")
    if incoming_rrp_price is not None or not partial_update or created:
        offer.rrp_price = incoming_rrp_price
    if item.currency or not partial_update or created:
        offer.currency = item.currency
    offer.is_available = item.is_available
    offer.is_discontinued = is_discontinued
    offer.extra = item.extra
    offer.last_imported_at = timezone.now()

    if not (partial_update and not created and offer.product_id):
        offer.product = _ensure_product(job, item, offer)

    if offer.rrp_price is not None:
        offer.selling_price = offer.rrp_price
    elif not partial_update or created:
        offer.selling_price = compute_selling_price(offer)
    if is_discontinued:
        offer.is_available = False
        offer.stock = 0
        offer.is_published = False
    elif offer.category_id and offer.selling_price is not None:
        offer.is_published = True
    offer.save()
    return offer


def _extra_text(extra: dict, key: str) -> str:
    value = extra.get(key) if isinstance(extra, dict) else ""
    if value is None:
        return ""
    return str(value)




def _extra_decimal(extra: dict, key: str) -> Decimal | None:
    if not isinstance(extra, dict):
        return None
    value = extra.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _is_partial_update(item: NormalizedItem) -> bool:
    if not isinstance(item.extra, dict):
        return False
    return bool(item.extra.get("partial_update"))


def _find_partial_offer_by_secondary_identity(job: ImportJob, item: NormalizedItem) -> Offer | None:
    source = str(item.extra.get("source", "")).strip().lower() if isinstance(item.extra, dict) else ""
    ean = (item.ean or "").strip()
    if source != "fox_stock" or not ean:
        return None
    return Offer.objects.filter(supplier=job.supplier, ean=ean).order_by("pk").first()


def _is_discontinued_item(item: NormalizedItem) -> bool:
    if not isinstance(item.extra, dict):
        return False
    status = str(item.extra.get("status", "")).strip().lower()
    source = str(item.extra.get("source", "")).strip().lower()
    return status == "discontinued" or source == "fox_discontinued"
