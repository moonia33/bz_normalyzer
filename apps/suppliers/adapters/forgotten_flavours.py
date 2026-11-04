from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import requests
from lxml import html as lxml_html

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://forgottenflavoursofficial.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
DEFAULT_IN_STOCK_QTY = 10
# Shopify edge rate-limiteris (429 'local_rate_limited', Retry-After).
_PAGE_LIMIT = 250

_SIZE_LETTERS = {
    "XS", "S", "M", "L", "XL", "XXL", "XXXL", "3XL", "4XL", "5XL",
    "SMALL", "MEDIUM", "LARGE", "X-LARGE", "OS", "ONE SIZE",
}
_COLOR_WORDS = {
    "green", "black", "camo", "olive", "grey", "gray", "red", "blue",
    "white", "yellow", "orange", "pink", "purple", "brown", "khaki",
    "navy", "denim", "charcoal", "beige", "cream", "sand", "brown",
}
_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")


def _axes_from_options(title: str) -> dict[str, str]:
    """'Krill 1kg'/'Olive / XL' varianto title -> ašys (size/color/variantas)."""
    axes: dict[str, str] = {}
    for part in re.split(r"\s*/\s*", title or ""):
        part = part.strip()
        if not part or part.lower() == "default title":
            continue
        if part.upper() in _SIZE_LETTERS:
            axes["size"] = part
        elif re.fullmatch(r"\d{1,3}", part):
            axes["size"] = part
        elif re.fullmatch(r"\d{1,3}\s*mm", part, re.I):
            # Hookbait diametras ('12mm') – parduotuvės 'size' filtrui.
            axes["size"] = part
        elif part.lower() in _COLOR_WORDS:
            axes["color"] = part.title()
        elif re.match(r"[\d.,]+\s*(kg|g|ml|mm|cm|m|l)\b", part, re.I):
            axes.setdefault("variantas", part)
        else:
            axes.setdefault("variantas", part)
    return axes


def _html_to_text(body: str) -> str:
    if not body:
        return ""
    text = lxml_html.fromstring(f"<div>{body}</div>").text_content()
    return _WS_RE.sub(" ", text).strip()


ECB_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


@register_adapter
class ForgottenFlavoursAdapter(BaseAdapter):
    """forgottenflavoursofficial.com (Shopify) viešo katalogo scraper'is.

    Feed'o ir B2B nėra – tik viešas Shopify katalogas:
    `/products.json?limit=250&page=N` (vienas request'as / 250 produktų).
    Pirkimo kainų nėra; public `price` (retail, su PVM) rašomas į
    `extra.rrp_price` – mūsų selling_price iš pricing rules.

    Stock feed'as per `/products/<handle>.js` paima `available` flag
    (products.json jo neturi). Kategorija = `product_type`/`tags`.

    Shopify edge rate-limitina agresyviai (429 'local_rate_limited',
    Retry-After) – gerbiam backoff + `request_delay`.

    config: base_url, request_delay (default 1.0), timeout, retries,
    in_stock_qty (default 10), currency (default GBP), brand,
    fetch_availability (bool; stock role'ui automatiškai true),
    max_products (testams).
    """

    code = "forgotten_flavours"

    # ------------------------------------------------------------------ fetch

    def fetch(self, uploaded_path: str | None = None) -> bytes:
        if uploaded_path or self.feed.file:
            return super().fetch(uploaded_path)

        timeout = int(self.config.get("timeout", 30))
        delay = float(self.config.get("request_delay", 1.0))
        retries = int(self.config.get("retries", 4))
        max_products = int(self.config.get("max_products") or 0)
        base_url = str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")

        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

        from apps.suppliers.models import FeedRole

        is_stock = getattr(self.feed, "role", "") == FeedRole.STOCK
        # Ir katalogo importas rašo likutį – be `available` išparduoti variantai gautų in_stock_qty.
        fetch_avail = bool(self.config.get("fetch_availability", True)) or is_stock

        products = self._products_json(session, base_url, timeout, retries, delay)
        if max_products:
            products = products[:max_products]
        if not products:
            raise AdapterError("Forgotten Flavours: produktų sąrašas tuščias.")

        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for index, product in enumerate(products, start=1):
            try:
                available = None
                if fetch_avail:
                    available = self._product_availability(
                        session, base_url, product.get("handle") or "", timeout, retries
                    )
                    if delay:
                        time.sleep(delay)
                items.extend(self._product_items(product, base_url, available))
            except Exception as exc:  # noqa: BLE001
                errors.append({"handle": str(product.get("handle")), "error": str(exc)[:300]})
                logger.warning("FF produkto klaida %s: %s", product.get("handle"), exc)

        payload = {"items": items, "errors": errors}
        # Shopify Markets kainas rodo pagal lankytojo šalį (iš srv2 – EUR).
        currency = self._store_currency(session, base_url, timeout) or str(
            self.config.get("currency") or "GBP"
        ).upper()
        if currency != "EUR":
            payload["currency"] = currency
            payload["rate_per_eur"] = str(self._ecb_rate(session, currency, timeout))
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _store_currency(session, base_url: str, timeout: int) -> str:
        """Valiuta, kuria ši sesija gauna products.json kainas (/cart.js)."""
        try:
            response = session.get(f"{base_url}/cart.js", timeout=timeout)
            response.raise_for_status()
            return str(response.json().get("currency") or "").upper()
        except (requests.RequestException, ValueError):
            return ""

    @staticmethod
    def _ecb_rate(session, currency: str, timeout: int) -> Decimal:
        """ECB dienos kursas: kiek `currency` vienetų už 1 EUR."""
        response = session.get(ECB_RATES_URL, timeout=timeout)
        response.raise_for_status()
        m = re.search(rf"currency=['\"]{currency}['\"]\s+rate=['\"]([\d.]+)['\"]", response.text)
        if not m:
            raise AdapterError(f"Forgotten Flavours: ECB kurso {currency} nėra – kainos nekonvertuojamos.")
        return Decimal(m.group(1))

    # ---------------------------------------------------------------- helpers

    def _get_json(self, session, url: str, timeout: int, retries: int) -> Any:
        """GET JSON su 429 Retry-After + HTTP>=500 backoff."""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = session.get(url, timeout=timeout)
                if response.status_code == 200 and response.text.strip().startswith(("{", "[")):
                    return response.json()
                retry_after = int(response.headers.get("Retry-After") or 0)
                last_exc = AdapterError(
                    f"HTTP {response.status_code}: {response.text[:80]}"
                )
                wait = max(retry_after, 5 * (attempt + 1))
                if attempt < retries:
                    time.sleep(wait)
            except AdapterError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < retries:
                    time.sleep(5 * (attempt + 1))
        raise last_exc or AdapterError(f"užklausa nepavyko: {url}")

    def _products_json(
        self, session, base_url: str, timeout: int, retries: int, delay: float
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            url = f"{base_url}/products.json?limit={_PAGE_LIMIT}&page={page}"
            data = self._get_json(session, url, timeout, retries)
            batch = (data or {}).get("products") or []
            out.extend(batch)
            if len(batch) < _PAGE_LIMIT:
                break
            page += 1
            if delay:
                time.sleep(delay)
        return out

    def _product_availability(
        self, session, base_url: str, handle: str, timeout: int, retries: int
    ) -> dict[int, bool] | None:
        if not handle:
            return None
        data = self._get_json(
            session, f"{base_url}/products/{handle}.js", timeout, retries
        )
        out: dict[int, bool] = {}
        for variant in (data or {}).get("variants") or []:
            if variant.get("id") is not None:
                out[int(variant["id"])] = bool(variant.get("available"))
        return out

    # ------------------------------------------------------------ page -> rows

    def _product_items(
        self, product: dict[str, Any], base_url: str, available: dict[int, bool] | None
    ) -> list[dict[str, Any]]:
        handle = product.get("handle") or ""
        title = (product.get("title") or "").strip()
        product_type = (product.get("product_type") or "").strip()
        raw_tags = product.get("tags") or []
        if isinstance(raw_tags, str):
            raw_tags = raw_tags.split(",")
        tags = [t.strip() for t in raw_tags if t and str(t).strip()]
        body = product.get("body_html") or ""
        images = [
            (img.get("src") or "").split("?")[0]
            for img in (product.get("images") or [])
            if img.get("src")
        ]
        url = f"{base_url}/products/{handle}" if handle else base_url

        out: list[dict[str, Any]] = []
        for variant in product.get("variants") or []:
            variant_id = variant.get("id")
            v_title = (variant.get("title") or "").strip()
            is_default = v_title.lower() == "default title"
            price = self._to_decimal(variant.get("price"))
            compare_at = self._to_decimal(variant.get("compare_at_price"))
            variant_available = True
            if available is not None and variant_id is not None:
                variant_available = available.get(int(variant_id), False)

            out.append(
                {
                    "external_id": str(variant_id) if variant_id else f"{handle}:{v_title}",
                    "variant_id": str(variant_id or ""),
                    "handle": handle,
                    "product_name": title,
                    "name": title if is_default else f"{title} {v_title}".strip(),
                    "variant_label": "" if is_default else v_title,
                    "product_group": handle,
                    "sku": (variant.get("sku") or "").strip(),
                    "ean": (variant.get("barcode") or "").strip(),
                    "price": str(price) if price is not None else "",
                    "compare_at_price": str(compare_at) if compare_at is not None else "",
                    "available": variant_available,
                    "grams": variant.get("grams"),
                    "product_type": product_type,
                    "vendor": (product.get("vendor") or "").strip(),
                    "tags": tags,
                    "description": _html_to_text(body),
                    "description_html": body,
                    "images": images,
                    "source_url": url,
                }
            )
        return out

    # ------------------------------------------------------------------- parse

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        data = json.loads(payload.decode("utf-8"))
        if isinstance(data, str):
            data = json.loads(data)
        self.fetch_errors = len(data.get("errors") or [])
        self._rate = Decimal(data["rate_per_eur"]) if data.get("rate_per_eur") else None
        seen: set[str] = set()
        for row in data.get("items") or []:
            external_id = (row.get("external_id") or "").strip()
            if not external_id or external_id in seen:
                continue
            seen.add(external_id)
            yield row

    # --------------------------------------------------------------- normalize

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        external_id = (row.get("external_id") or "").strip()
        if not external_id:
            raise AdapterError(f"FF: trūksta external_id eilutėje: {repr(row)[:200]}")

        name = (row.get("name") or row.get("product_name") or "").strip() or external_id
        product_type = (row.get("product_type") or "").strip()
        category_path = product_type or "Uncategorized"
        tags = [t for t in (row.get("tags") or []) if t]

        axes = _axes_from_options(row.get("variant_label") or "")
        in_stock_qty = int(self.config.get("in_stock_qty", DEFAULT_IN_STOCK_QTY))
        available = bool(row.get("available", True))
        rrp = self._to_decimal(row.get("price"))
        rate = getattr(self, "_rate", None)
        if rrp is not None and rate:
            rrp = (rrp / rate).quantize(Decimal("0.01"))
        images = [u for u in (row.get("images") or []) if u]
        grams = row.get("grams")

        # Tiekėjo SKU jau turi 'FF-' prefiksą ('FF-PU-MC15') – nuimam,
        # kad shop kodas būtų 'FR.PU-MC15', ne 'FR.FF-PU-MC15'.
        raw_sku = (row.get("sku") or "").strip()
        sku = raw_sku[3:] if raw_sku.upper().startswith("FF-") else raw_sku

        extra = {
            "identifiers": {
                "variant_id": row.get("variant_id"),
                "handle": row.get("handle"),
            },
            "base_name": row.get("product_name"),
            "product_group": row.get("product_group"),
            "sub_category_raw": product_type,
            "variant_label": row.get("variant_label"),
            "sku_raw": row.get("sku"),
            "rrp_price": str(rrp) if rrp is not None else None,
            "compare_at_price": row.get("compare_at_price"),
            "stock_text": "in_stock" if available else "out_of_stock",
            "source_url": row.get("source_url"),
            "main_image_url": images[0] if images else "",
            "unit_weight_raw": f"{round(Decimal(grams) / 1000, 4)}kg" if grams else "",
            "tags": ", ".join(tags),
        }
        return NormalizedItem(
            external_id=external_id,
            sku=sku or external_id,
            name=name,
            ean=(row.get("ean") or "").strip(),
            brand_name=str(self.config.get("brand") or "Forgotten Flavours"),
            category_path=category_path,
            description=(row.get("description") or "").strip(),
            attributes={"axes": axes} if axes else {},
            images=images,
            stock=in_stock_qty if available else 0,
            purchase_price=None,  # pirkimo kainų nėra – tik public retail
            currency="EUR",
            is_available=available,
            extra={k: v for k, v in extra.items() if v not in (None, "", {})},
            raw=row,
        )
