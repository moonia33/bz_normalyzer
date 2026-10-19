from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.catalog.models import Product, ProductText
from apps.corecfg.models import Language
from apps.ingestion.dto import NormalizedItem
from apps.ingestion.models import ImportJob, Offer, OfferText, RawItem
from apps.mapping.models import ProductLink
from apps.mapping.services import linked_product, map_brand, map_category
from apps.pricing.engine import compute_selling_price
from apps.suppliers.adapters.base import AdapterError
from apps.suppliers.adapters.resolver import resolve_adapter
from apps.suppliers.models import FeedRole, SupplierFeed

logger = logging.getLogger(__name__)

MAX_ERROR_ITEMS = 100
TEXT_FIELDS = ("name", "base_name", "description", "technical_description", "combined_full_text")


@dataclass
class FeedContext:
    """Iš feed'o konfigūracijos išvesta importavimo semantika."""

    feed: SupplierFeed
    role: str
    language: Language | None
    base_language: Language | None

    @property
    def is_partial(self) -> bool:
        return self.role in {FeedRole.STOCK, FeedRole.DISCONTINUED}

    @property
    def is_discontinued(self) -> bool:
        return self.role == FeedRole.DISCONTINUED

    @property
    def is_translation(self) -> bool:
        return (
            self.language is not None
            and self.base_language is not None
            and self.language_id != self.base_language_id
        )

    @property
    def language_id(self) -> int | None:
        return self.language.id if self.language else None

    @property
    def base_language_id(self) -> int | None:
        return self.base_language.id if self.base_language else None


def get_base_language() -> Language | None:
    return Language.objects.filter(is_base=True).first() or Language.objects.filter(
        is_active=True
    ).first()


def expand_feed_to_group(feed: SupplierFeed) -> list[SupplierFeed]:
    """Grąžina visus aktyvius tos pačios grupės feed'us importavimo tvarka.

    Kalbiniai feed'ai visada importuojami kartu; bazinės kalbos – pirmiausia.
    Feed'as be grupės grąžina tik save.
    """
    if not feed.feed_group:
        return [feed]
    group = list(
        SupplierFeed.objects.filter(
            supplier=feed.supplier,
            feed_group=feed.feed_group,
            role=feed.role,
            is_active=True,
        ).select_related("language")
    )
    base = get_base_language()

    def sort_key(f: SupplierFeed) -> tuple[int, str]:
        if base and f.language_id == base.id:
            return (0, f.name)
        if f.language_id is None:
            return (1, f.name)
        return (2, f.name)

    group.sort(key=sort_key)
    return group


def _make_context(feed: SupplierFeed) -> FeedContext:
    return FeedContext(
        feed=feed,
        role=feed.role or FeedRole.CATALOG,
        language=feed.language,
        base_language=get_base_language(),
    )


def run_import(job: ImportJob) -> ImportJob:
    job.status = ImportJob.Status.RUNNING
    job.started_at = timezone.now()
    job.error_message = ""
    job.save(update_fields=["status", "started_at", "error_message", "updated_at"])

    stats = {
        "normalized": 0,
        "upserted": 0,
        "unchanged": 0,
        "skipped_partial_missing": 0,
        "unmapped_categories": 0,
        "translations": 0,
        "errors": 0,
        "error_items": [],
    }
    try:
        feed = _resolve_feed(job)
        ctx = _make_context(feed)
        adapter = resolve_adapter(feed)
        uploaded = job.uploaded_file.path if job.uploaded_file else None
        payload = adapter.fetch(uploaded_path=uploaded)

        for row_no, item in enumerate(adapter.iter_items(payload), start=1):
            stats["normalized"] += 1
            try:
                with transaction.atomic():
                    _store_raw(job, item)
                    offer = _upsert_offer(job, item, stats, ctx)
                    if offer is not None:
                        stats["upserted"] += 1
            except Exception as exc:
                stats["errors"] += 1
                _record_error(stats, row_no, item, exc)

        feed.last_success_at = timezone.now()
        feed.save(update_fields=["last_success_at", "updated_at"])
        job.stats = stats
        job.status = ImportJob.Status.SUCCESS
        job.finished_at = timezone.now()
        job.save(update_fields=["stats", "status", "finished_at", "updated_at"])
    except Exception as exc:
        logger.exception("Import job #%s failed", job.pk)
        job.stats = stats
        job.status = ImportJob.Status.FAILED
        job.error_message = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["stats", "status", "error_message", "finished_at", "updated_at"])
        raise
    return job


def _record_error(stats: dict, row_no: int, item: NormalizedItem, exc: Exception) -> None:
    logger.exception(
        "Import item failed (row=%s, external_id=%s): %s",
        row_no,
        getattr(item, "external_id", ""),
        exc,
    )
    error_items = stats.setdefault("error_items", [])
    if len(error_items) < MAX_ERROR_ITEMS:
        error_items.append(
            {
                "row_no": row_no,
                "external_id": getattr(item, "external_id", ""),
                "error": str(exc)[:500],
            }
        )


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

    # EAN – pirminis kryžminis (tarp tiekėjų) identiteto raktas.
    if offer.ean:
        product = Product.objects.filter(ean__iexact=offer.ean).order_by("pk").first()
        if product:
            return product

    # art-no (sku) – tas pats iš bet kurio tiekėjo = ta pati prekė.
    product = Product.objects.filter(sku=item.external_id).order_by("pk").first()
    if not product:
        product = Product.objects.filter(
            source_external_id=item.external_id
        ).order_by("pk").first()
    if not product:
        product = (
            ProductLink.objects.filter(external_id=item.external_id)
            .select_related("product")
            .order_by("product_id")
            .values_list("product_id", flat=True)
            .first()
        )
        product = Product.objects.filter(pk=product).first() if product else None
    if product:
        return product

    # Heuristikos tik tarp šio tiekėjo jau susietų prekių –
    # kad skirtingų tiekėjų product_group kodai nesusikirstų.
    supplier_products = Product.objects.filter(supplier_links__supplier=job.supplier)

    # base_name tuščias -> 100% ne variantas (atsarginės dalys ir pan.)
    # – visada atskiras produktas, neskaitant identiteto raktų aukščiau.
    if not offer.base_name:
        return None

    candidate_filters: list[Q] = []

    if offer.product_group and offer.base_name:
        # Kai yra sub_category – reikalaujame sutapti, kad
        # pvz. reel'ai ir jų spare spool'ai nesusilietų į vieną prekę.
        if offer.sub_category_raw:
            candidate_filters.append(
                Q(product_group__iexact=offer.product_group)
                & Q(base_name__iexact=offer.base_name)
                & Q(sub_category_raw__iexact=offer.sub_category_raw)
            )
        else:
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

    def sub_category_ok(product) -> bool:
        offer_sub = (offer.sub_category_raw or "").strip().lower()
        product_sub = (product.sub_category_raw or "").strip().lower()
        # Taisykli tik plokščioms sub taksonomijoms (FOX): hierarchiniai
        # '>' sub'ai (MOSS) gali skirtis tame pačiame produkte.
        if ">" in offer_sub or ">" in product_sub:
            return True
        # Jei abu turi sub_category – privalo sutapti (kitaip reel'is
        # su liestų su spare spool'u ir pan.)
        return not (offer_sub and product_sub and offer_sub != product_sub)

    for candidate in candidate_filters:
        product = supplier_products.filter(candidate).order_by("pk").first()
        if product and sub_category_ok(product):
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
        product = supplier_products.filter(filters).order_by("pk").first()
        if product:
            return product

    return None


def _ensure_product(job: ImportJob, item: NormalizedItem, offer: Offer) -> Product:
    product = _find_matching_product(job, item, offer) or offer.product
    if not product:
        # SKU = tiekėjo art-no; supplier prefiksas tik kolizijos atveju
        # (tas pats art-no iš kito tiekėjo jau turi produktą).
        sku = item.external_id[:64]
        if Product.objects.filter(sku=sku).exists():
            sku = f"{job.supplier.code}-{item.external_id}"[:64]
        product, _ = Product.objects.get_or_create(
            sku=sku,
            defaults={
                # Product Description (base_name) = bendras produkto vardas;
                # Name = varianto vardas (offer.name).
                "name": offer.base_name or item.name,
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
                "videos": list(offer.videos) if isinstance(offer.videos, list) else [],
            },
        )

    if item.external_id and (not product.source_external_id or product.source_external_id == item.external_id):
        product.source_external_id = item.external_id

    changed_fields: list[str] = []
    product_name = offer.base_name or item.name
    if product_name and product.name != product_name:
        product.name = product_name
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
    if isinstance(offer.videos, list):
        merged_videos = _merge_video_lists(product.videos, offer.videos)
        if product.videos != merged_videos:
            product.videos = merged_videos
            changed_fields.append("videos")
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


def _content_hash(item: NormalizedItem) -> str:
    """Turinio hash change detection'ui – be volatilių laukų (stock/availability)."""
    payload = {
        "name": item.name,
        "ean": item.ean,
        "sku": item.sku,
        "brand_name": item.brand_name,
        "category_path": item.category_path,
        "description": item.description,
        "attributes": item.attributes,
        "images": item.images,
        "videos": item.videos,
        "purchase_price": str(item.purchase_price) if item.purchase_price is not None else None,
        "currency": item.currency,
        "extra": {
            k: v
            for k, v in (item.extra or {}).items()
            if k not in {"status", "raw_date", "is_available"}
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def _net_price(value: Decimal | None, includes_vat: bool, vat_rate: Decimal) -> Decimal | None:
    if value is None or not includes_vat:
        return value
    divisor = Decimal("1") + vat_rate
    return (value / divisor).quantize(Decimal("0.0001"))


def _item_text_fields(item: NormalizedItem) -> dict[str, str]:
    return {
        "name": (item.name or "").strip(),
        "base_name": _extra_text(item.extra, "base_name"),
        "description": (item.description or "").strip(),
        "technical_description": _extra_text(item.extra, "technical_description"),
        "combined_full_text": _extra_text(item.extra, "combined_full_text"),
    }


def _upsert_offer_text(
    job: ImportJob,
    item: NormalizedItem,
    offer: Offer,
    language: Language,
) -> None:
    fields = _item_text_fields(item)
    if not any(fields.values()):
        return
    OfferText.objects.update_or_create(
        offer=offer,
        language=language,
        defaults=fields,
    )
    if offer.product_id:
        _propagate_product_text(offer, language, fields)


def _propagate_product_text(offer: Offer, language: Language, fields: dict[str, str]) -> None:
    from django.utils.text import slugify

    text = ProductText.objects.filter(product_id=offer.product_id, language=language).first()
    if text is None:
        ProductText.objects.create(
            product_id=offer.product_id,
            language=language,
            source_offer=offer,
            slug=slugify(fields["name"])[:220] if fields.get("name") else "",
            **fields,
        )
        return
    # Perrašome tik jei šis offer'is buvo to vertimo šaltinis arba tekstas tuščias.
    if text.source_offer_id not in (None, offer.id):
        return
    changed = []
    for key, value in fields.items():
        if value and getattr(text, key) != value:
            setattr(text, key, value)
            changed.append(key)
    if not text.slug and fields.get("name"):
        text.slug = slugify(fields["name"])[:220]
        changed.append("slug")
    if changed:
        text.source_offer = offer
        text.save(update_fields=[*changed, "source_offer", "updated_at"])


def _upsert_offer(
    job: ImportJob,
    item: NormalizedItem,
    stats: dict,
    ctx: FeedContext,
) -> Offer | None:
    partial_update = ctx.is_partial
    offer = Offer.objects.filter(supplier=job.supplier, external_id=item.external_id).first()
    if offer is None and partial_update:
        offer = _find_partial_offer_by_secondary_identity(job, item)

    if ctx.is_translation:
        if offer is None:
            # Kalbinis feed'as be bazinės prekės – sukurti minimalų offer,
            # kad tekstai nepradingtų; publikuojama tik kai atsiranda bazė.
            offer = Offer.objects.create(
                supplier=job.supplier,
                external_id=item.external_id,
                name=item.name,
                sku=item.sku or item.external_id,
                is_published=False,
            )
        _upsert_offer_text(job, item, offer, ctx.language)
        _merge_offer_videos(offer, item.videos)
        stats["translations"] += 1
        offer.last_imported_at = timezone.now()
        offer.save(update_fields=["last_imported_at", "updated_at"])
        return offer

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

    is_discontinued = ctx.is_discontinued or _is_discontinued_item(item)
    supplier = job.supplier
    purchase_price = _net_price(
        item.purchase_price,
        supplier.purchase_price_includes_vat,
        supplier.vat_rate,
    )

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

    # Change detection: jei turinys nepasikeitė – atnaujiname tik
    # volatilius laukus (stock/availability), be produkto sinchronizacijos.
    new_hash = _content_hash(item)
    if not created and offer.content_hash == new_hash and not is_discontinued:
        volatile_changed = []
        if offer.stock != item.stock:
            offer.stock = item.stock
            volatile_changed.append("stock")
        if offer.availability_date != item.availability_date:
            offer.availability_date = item.availability_date
            volatile_changed.append("availability_date")
        if offer.is_available != item.is_available:
            offer.is_available = item.is_available
            volatile_changed.append("is_available")
        offer.last_imported_at = timezone.now()
        volatile_changed.append("last_imported_at")
        offer.save(update_fields=[*volatile_changed, "updated_at"])
        stats["unchanged"] = int(stats.get("unchanged", 0)) + 1
        return offer

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
    incoming_pack_unit = _extra_text(item.extra, "pack_unit")
    incoming_pack_quantity = _extra_int(item.extra, "pack_quantity")
    incoming_trade_pack_qty = _extra_int(item.extra, "trade_pack_qty")
    incoming_pack_barcode = _extra_text(item.extra, "pack_barcode")
    incoming_sold_as_pack = bool(
        (item.extra or {}).get("sold_as_pack")
        if isinstance(item.extra, dict)
        else False
    )
    if incoming_pack_unit or not partial_update or created:
        offer.pack_unit = incoming_pack_unit
    if incoming_pack_quantity is not None or not partial_update or created:
        offer.pack_quantity = incoming_pack_quantity
    if incoming_trade_pack_qty is not None or not partial_update or created:
        offer.trade_pack_qty = incoming_trade_pack_qty
    if incoming_pack_barcode or not partial_update or created:
        offer.pack_barcode = incoming_pack_barcode
    if not partial_update or created:
        offer.sold_as_pack = incoming_sold_as_pack
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
    if item.videos or not partial_update or created:
        offer.videos = item.videos

    offer.stock = item.stock
    offer.availability_date = item.availability_date
    if purchase_price is not None or not partial_update or created:
        offer.purchase_price = purchase_price
    incoming_rrp_price = _extra_decimal(item.extra, "rrp_price")
    if incoming_rrp_price is not None or not partial_update or created:
        offer.rrp_price = incoming_rrp_price
    if item.currency or not partial_update or created:
        offer.currency = item.currency
    offer.is_available = item.is_available
    offer.is_discontinued = is_discontinued
    offer.extra = item.extra
    offer.content_hash = new_hash
    offer.last_imported_at = timezone.now()

    if not (partial_update and not created and offer.product_id):
        offer.product = _ensure_product(job, item, offer)

    # RRP yra tik informacinė rekomenduota kaina – selling_price visada iš taisyklių.
    if not partial_update or created:
        offer.selling_price = compute_selling_price(offer)
    spare_part = bool((item.attributes or {}).get("spare_part")) or (
        offer.product_group == "" and offer.supplier.code in ("fox", "ccm")
    )
    if is_discontinued or spare_part:
        offer.is_available = False if is_discontinued else offer.is_available
        if is_discontinued:
            offer.stock = 0
        # Atsarginės dalys / nutrauktos prekės nepublikuojamos.
        offer.is_published = False
    elif offer.category_id and offer.selling_price is not None and offer.images:
        # Prekė be nuotraukų nepublikuojama (neeksportuojama į shop).
        offer.is_published = True
    else:
        offer.is_published = False
    offer.save()
    return offer


def _merge_video_lists(existing: list | None, incoming: list | None) -> list[dict]:
    """Sujungia video sąrašus, dedupuoja pagal URL. Turinys [{url,type,language}]."""
    merged: list[dict] = []
    seen: set[str] = set()
    for source in (existing, incoming):
        for video in source or []:
            url = str((video or {}).get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append(
                {
                    "url": url,
                    "type": str(video.get("type") or "product"),
                    "language": str(video.get("language") or ""),
                }
            )
    return merged


def _merge_offer_videos(offer: Offer, incoming: list | None) -> None:
    """Kalbinis feed'as papildo offer.videos savo kalbos video URL'ais."""
    merged = _merge_video_lists(offer.videos, incoming)
    if merged != offer.videos:
        offer.videos = merged
        offer.save(update_fields=["videos", "updated_at"])
        if offer.product_id:
            product = offer.product
            product_merged = _merge_video_lists(product.videos, incoming)
            if product_merged != product.videos:
                product.videos = product_merged
                product.save(update_fields=["videos", "updated_at"])


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


def _extra_int(extra: dict, key: str) -> int | None:
    value = _extra_decimal(extra, key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _find_partial_offer_by_secondary_identity(job: ImportJob, item: NormalizedItem) -> Offer | None:
    """Stock feed'ams – antrinis matching'as pagal EAN (kai external_id skiriasi)."""
    ean = (item.ean or "").strip()
    if not ean:
        return None
    return Offer.objects.filter(supplier=job.supplier, ean=ean).order_by("pk").first()


def _is_discontinued_item(item: NormalizedItem) -> bool:
    if not isinstance(item.extra, dict):
        return False
    status = str(item.extra.get("status", "")).strip().lower()
    return status == "discontinued"
