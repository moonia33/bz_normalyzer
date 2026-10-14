from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

from apps.catalog.models import Product
from apps.corecfg.models import Language, get_config
from apps.ingestion.models import Offer
from apps.suppliers.models import Supplier

from .models import ExportRun

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
CATALOG_PATH = "/api/v1/ops/imports/catalog"
STOCK_PATH = "/api/v1/ops/imports/stock"
DEFAULT_TAX_CLASS = "standard"
# Kol nėra LT vertimų pipeline – lt transliacija pildoma baziniu tekstu.
FALLBACK_TRANSLATION_LANG = "lt"


def _net(value: Decimal | None, includes_vat: bool, vat_rate: Decimal) -> Decimal | None:
    if value is None:
        return None
    if not includes_vat:
        return value.quantize(Decimal("0.01"))
    return (value / (Decimal("1") + vat_rate)).quantize(Decimal("0.01"))


def _warehouse_code(supplier: Supplier) -> str:
    return supplier.export_warehouse_code or supplier.code


def _translation_block(product: Product, base_lang: str) -> dict:
    """translations{lang: {name, slug, description}} pagal format_version 1."""
    texts: dict[str, dict] = {}
    for text in product.texts.select_related("language"):
        code = text.language.code
        texts[code] = {
            "name": text.name,
            "slug": text.slug or slugify(text.name)[:220],
            "description": text.description,
        }
    base = {
        "name": product.name,
        "slug": slugify(product.name)[:220],
        "description": product.description,
    }
    texts.setdefault(base_lang, base)
    # Shop reikalauja lt; kol nėra vertimų pipeline – bazinis tekstas.
    texts.setdefault(FALLBACK_TRANSLATION_LANG, dict(base))
    return texts


def _features(product: Product) -> list[dict]:
    specs = (product.attributes or {}).get("specs")
    if not isinstance(specs, dict):
        return []
    features = []
    for key, value in specs.items():
        if value in (None, ""):
            continue
        code = slugify(str(key))[:64] or str(key).strip().lower().replace(" ", "_")[:64]
        if code:
            features.append({"code": code, "value": str(value)})
    return features


def _images(product: Product) -> list[dict]:
    urls = product.images if isinstance(product.images, list) else []
    if product.main_image_url and product.main_image_url not in urls:
        urls = [product.main_image_url, *urls]
    return [
        {"url": url, "alt": product.name, "sort_order": index}
        for index, url in enumerate(urls)
        if url
    ]


def _offer_variant_code(offer: Offer) -> str:
    return (offer.sku or "").strip() or f"{offer.supplier.code}-{offer.external_id}"


def _variant_key(offer: Offer) -> str:
    """Tas pats EAN iš kelių tiekėjų = tas pats variantas (keli sandėliai)."""
    if offer.ean:
        return f"ean:{offer.ean.strip().lower()}"
    return f"sku:{offer.supplier.code}:{_offer_variant_code(offer)}"


def _build_variants(offers: list[Offer]) -> list[dict]:
    variants: dict[str, dict] = {}
    order: list[str] = []
    for offer in offers:
        key = _variant_key(offer)
        warehouse = _warehouse_code(offer.supplier)
        stock = max(offer.stock or 0, 0) if offer.is_available else 0
        existing = variants.get(key)
        if existing is None:
            price_net = _net(
                offer.selling_price,
                offer.supplier.selling_price_includes_vat,
                offer.supplier.vat_rate,
            )
            options = {"size": offer.size_raw} if offer.size_raw else {}
            existing = {
                "code": _offer_variant_code(offer),
                "product_group_code": offer.product_group or "",
                "ean": offer.ean or "",
                "options": options,
                "price_net": str(price_net) if price_net is not None else None,
                "stock": {},
            }
            variants[key] = existing
            order.append(key)
        existing["stock"][warehouse] = int(existing["stock"].get(warehouse, 0)) + int(stock)
    return [variants[key] for key in order]


def _item_price_net(product: Product, offers: list[Offer]) -> str | None:
    """Item-level price_net – pirminio (default) varianto kaina."""
    primary = next(
        (o for o in offers if o.external_id == product.source_external_id),
        offers[0] if offers else None,
    )
    if primary is None:
        return None
    price = _net(
        primary.selling_price,
        primary.supplier.selling_price_includes_vat,
        primary.supplier.vat_rate,
    )
    return str(price) if price is not None else None


def _item_cost_net(product: Product, offers: list[Offer]) -> str | None:
    primary = next(
        (o for o in offers if o.external_id == product.source_external_id),
        offers[0] if offers else None,
    )
    cost = product.purchase_price
    if cost is None and primary is not None:
        cost = primary.purchase_price
    return str(cost.quantize(Decimal("0.01"))) if cost is not None else None


def build_catalog_payload(supplier_code: str | None = None) -> dict:
    base_lang = (Language.objects.filter(is_base=True).first())
    base_code = base_lang.code if base_lang else "en"
    tax_class = get_config("default_tax_class", DEFAULT_TAX_CLASS)

    products_qs = (
        Product.objects.filter(is_active=True)
        .select_related("brand", "category")
        .prefetch_related("texts__language", "offers__supplier")
        .filter(offers__is_published=True)
        .distinct()
    )
    if supplier_code:
        products_qs = products_qs.filter(offers__supplier__code=supplier_code)

    items: list[dict] = []
    for product in products_qs.iterator(chunk_size=500):
        offers = [
            offer
            for offer in product.offers.all()
            if offer.is_published and not offer.is_discontinued
        ]
        if supplier_code:
            offers = [o for o in offers if o.supplier.code == supplier_code]
        if not offers:
            continue

        variants = _build_variants(offers)
        weight_g = None
        if product.shipping_weight_kg is not None:
            weight_g = int((product.shipping_weight_kg * 1000).to_integral_value())

        items.append(
            {
                "code": product.sku,
                "product_group_code": product.product_group or "",
                "ean": product.ean or "",
                "brand_code": product.brand.slug if product.brand_id else "",
                "category": product.category.slug if product.category_id else "",
                "tax_class": tax_class,
                "is_active": product.is_active,
                "price_net": _item_price_net(product, offers),
                "cost_net": _item_cost_net(product, offers),
                "weight_g": weight_g,
                "translations": _translation_block(product, base_code),
                "features": _features(product),
                "images": _images(product),
                "variants": variants,
            }
        )

    return {
        "format_version": FORMAT_VERSION,
        "source": "normalyzer",
        "mode": "upsert",
        "generated_at": timezone.now().isoformat(),
        "items": items,
    }


def build_stock_payload(supplier_code: str) -> dict:
    supplier = Supplier.objects.get(code=supplier_code)
    warehouse = _warehouse_code(supplier)
    offers = Offer.objects.filter(
        supplier=supplier,
        is_published=True,
        is_discontinued=False,
    )
    items = [
        {
            "code": _offer_variant_code(offer),
            "qty": max(offer.stock or 0, 0) if offer.is_available else 0,
        }
        for offer in offers.iterator()
    ]
    return {
        "format_version": FORMAT_VERSION,
        "warehouse_code": warehouse,
        "items": items,
    }


def payload_sha256(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def push_to_shop(path: str, payload: dict, timeout: int = 120) -> dict:
    base_url = (settings.SHOP_API_BASE_URL or "").rstrip("/")
    if not base_url:
        raise RuntimeError("SHOP_API_BASE_URL nenustatytas (.env).")
    headers = {"Content-Type": "application/json"}
    if settings.SHOP_API_KEY:
        headers["X-API-Key"] = settings.SHOP_API_KEY
    response = requests.post(
        f"{base_url}{path}",
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"status": response.status_code, "body": response.text[:500]}


def run_export(
    kind: str,
    payload: dict,
    *,
    push: bool = False,
    file_name: str = "",
    triggered_by: str = "",
    options: dict | None = None,
) -> ExportRun:
    run = ExportRun.objects.create(
        kind=kind,
        file_name=file_name,
        file_sha256=payload_sha256(payload),
        item_count=len(payload.get("items", [])),
        options=options or {},
        triggered_by=triggered_by,
    )
    if push:
        path = CATALOG_PATH if kind == ExportRun.Kind.CATALOG else STOCK_PATH
        try:
            run.shop_response = push_to_shop(path, payload)
            run.status = ExportRun.Status.PUSHED
        except Exception as exc:
            logger.exception("Eksporto push nepavyko (run #%s)", run.pk)
            run.status = ExportRun.Status.FAILED
            run.error_message = str(exc)
    run.save()
    return run
