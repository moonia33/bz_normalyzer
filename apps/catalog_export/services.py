from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal, InvalidOperation

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


def _videos(product: Product) -> list[dict]:
    """YouTube/video URL'ai: [{url, type, language}], dedup pagal url."""
    videos = product.videos if isinstance(product.videos, list) else []
    result = []
    seen: set[str] = set()
    for video in videos:
        url = str((video or {}).get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(
            {
                "url": url,
                "type": str(video.get("type") or "product"),
                "language": str(video.get("language") or ""),
            }
        )
    return result


def _offer_variant_code(offer: Offer) -> str:
    """Variant code = shop Variant.sku. Supplier prefiksas – FOX/CCM Art-No. gali dubliuotis."""
    sku = (offer.sku or "").strip() or offer.external_id
    return f"{offer.supplier.code}-{sku}"


def _to_int_cm(value) -> int:
    if value is None:
        return 0
    try:
        return int(Decimal(str(value)).to_integral_value())
    except (InvalidOperation, ValueError):
        return 0


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
                "cost_net": (
                    str(offer.purchase_price.quantize(Decimal("0.01")))
                    if offer.purchase_price is not None
                    else None
                ),
                "weight_g": _to_int_cm(
                    (offer.shipping_weight_kg * 1000) if offer.shipping_weight_kg else None
                ),
                "height_cm": _to_int_cm(offer.shipping_height_cm),
                "width_cm": _to_int_cm(offer.shipping_width_cm),
                "length_cm": _to_int_cm(offer.shipping_depth_cm),
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
                "weight_g": weight_g or 0,
                "height_cm": _to_int_cm(product.shipping_height_cm),
                "width_cm": _to_int_cm(product.shipping_width_cm),
                "length_cm": _to_int_cm(product.shipping_depth_cm),
                "translations": _translation_block(product, base_code),
                "features": _features(product),
                "images": _images(product),
                "videos": _videos(product),
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


class ShopClient:
    """HTTP klientas shop ops API.

    Shop'as naudoja JWT per HttpOnly cookie (accounts.JWTAuth): login
    `POST /api/v1/auth/login` {email, password} -> `access_token` cookie
    (~15 min), refresh per `POST /api/v1/auth/refresh`.
    Reikia staff vartotojo (_require_ops_user: is_staff/is_superuser).
    """

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.SHOP_API_BASE_URL or "").rstrip("/")
        if not self.base_url:
            raise RuntimeError("SHOP_API_BASE_URL nenustatytas (.env).")
        self.session = requests.Session()
        self._logged_in = False

    def _login(self) -> None:
        email = getattr(settings, "SHOP_USER_EMAIL", "") or ""
        password = getattr(settings, "SHOP_USER_PASSWORD", "") or ""
        if not email or not password:
            raise RuntimeError("SHOP_USER_EMAIL / SHOP_USER_PASSWORD nenustatyti (.env).")
        response = self.session.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"email": email, "password": password},
            timeout=30,
        )
        response.raise_for_status()
        self._logged_in = True

    def post(self, path: str, payload: dict, timeout: int = 120) -> dict:
        if not self._logged_in:
            self._login()
        response = self.session.post(
            f"{self.base_url}{path}", json=payload, timeout=timeout
        )
        if response.status_code == 401:
            # access token galėjo baigtis – relogin ir kartojam
            self._login()
            response = self.session.post(
                f"{self.base_url}{path}", json=payload, timeout=timeout
            )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {"status": response.status_code, "body": response.text[:500]}

    def get(self, path: str, timeout: int = 60):
        if not self._logged_in:
            self._login()
        response = self.session.get(f"{self.base_url}{path}", timeout=timeout)
        if response.status_code == 401:
            self._login()
            response = self.session.get(f"{self.base_url}{path}", timeout=timeout)
        response.raise_for_status()
        return response.json()


def push_to_shop(path: str, payload: dict, timeout: int = 120) -> dict:
    return ShopClient().post(path, payload, timeout=timeout)


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
