from __future__ import annotations

import html as html_lib
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

DEFAULT_BASE_URL = "https://holdcarp.sk/gb"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
DEFAULT_IN_STOCK_QTY = 10
MAX_PRODUCTS_PER_CATEGORY = 5000

# Variantų nuoroda kategorijos puslapyje:
# /gb/markers/22-122-slug-8588007219301.html#/18-colour-pink/26-dimension-4m
#   id_product=22, id_product_attribute=122, ean=8588007219301
_PRODUCT_URL_RE = re.compile(
    r"/(\d+)(?:-(\d+))?-([a-z0-9-]+?)(?:-(\d{8,14}))?\.html(#/[^\s\"']+)?",
    re.I,
)
_CATEGORY_URL_RE = re.compile(r"/gb/(\d+)-([a-z0-9-]+)/?$", re.I)
_DATA_PRODUCT_RE = re.compile(r'data-product="(\{&quot;.*?\})"', re.S)
_EUR_PRICE_RE = re.compile(r"([\d][\d\s.,]*)")
_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<[^>]+>")

_AXIS_MAP = {
    "colour": "color",
    "color": "color",
    "size": "size",
    "dimension": "variantas",
    "paper_type": "variantas",
    "paper type": "variantas",
}
_SKIP_CATEGORY_SLUGS = {"uvodna-stranka"}


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text or "").strip()


_BREADCRUMB_JSON_RE = re.compile(
    r'"breadcrumb"\s*:\s*\{\s*"links"\s*:\s*\[(.*?)\]\s*\}', re.S
)
_BREADCRUMB_LINK_RE = re.compile(r'"title"\s*:\s*"([^"]+)"')


def _page_breadcrumbs(text: str) -> list[str]:
    """PrestaShop 'breadcrumb' JSON blokas -> [{title}] -> kelias."""
    m = _BREADCRUMB_JSON_RE.search(text)
    if not m:
        return []
    return [
        c for c in (_clean(t) for t in _BREADCRUMB_LINK_RE.findall(m.group(1)))
        if c and c.lower() != "home"
    ]


def _html_to_text(body: str) -> str:
    if not body:
        return ""
    try:
        text = lxml_html.fromstring(f"<div>{body}</div>").text_content()
    except Exception:  # noqa: BLE001
        text = _TAG_RE.sub(" ", body)
    return _clean(text)


def _fragment_attrs(fragment: str) -> dict[str, str]:
    """'#/18-colour-pink/26-dimension-4m' -> {'colour': 'pink', 'dimension': '4m'}."""
    attrs: dict[str, str] = {}
    for part in (fragment or "").lstrip("#").strip("/").split("/"):
        m = re.match(r"\d+-([a-z_]+?)-(.+)$", part)
        if m:
            group = m.group(1).replace("_", " ")
            value = m.group(2).replace("-", " ").replace("m ", "m")
            # '37m' -> '3.7m', '57m' -> '5.7m' (PrestaShop slug be taško)
            if group == "dimension" and re.fullmatch(r"\d{2,3}m", value):
                value = value[:-2] + "." + value[-2] + "m" if len(value[:-2]) == 2 else value
            attrs.setdefault(group, value.strip())
    return attrs


def _decode_data_product(text: str) -> dict[str, Any]:
    """`<div id=product-details data-product="{&quot;...}">` -> dict."""
    m = _DATA_PRODUCT_RE.search(text)
    if not m:
        return {}
    try:
        return json.loads(html_lib.unescape(m.group(1)))
    except Exception:  # noqa: BLE001
        return {}


@register_adapter
class HoldCarpAdapter(BaseAdapter):
    """holdcarp.sk (PrestaShop, viešas) scraper'is.

    Feed'o/B2B nėra – tik viešas katalogas. Pirkimo kainų nėra; retail kaina
    (EUR, su PVM) -> `extra.rrp_price`, selling iš PriceRule taisyklių.

    Enumeracija: kategorijų puslapiai (`?resultsPerPage=9999999`) ->
    variantų nuorodos `/gb/cat/{id}-{id_attr}-slug-{ean}.html#/attrs`.
    Per produktą: `data-product` JSON (name, description, quantity, price,
    attributes). Per kombinaciją: POST `?action=refresh&ajax=1&
    id_product_attribute=N` -> `product_details` JSON (reference=komb. SKU,
    attributes group->name, quantity, price).

    config: base_url, request_delay (default 0.4), timeout, retries,
    in_stock_qty (default 10), brand, max_products (testams),
    skip_category_ids (default [2,18,22] – home/promo).
    """

    code = "holdcarp"

    # ------------------------------------------------------------------ fetch

    def fetch(self, uploaded_path: str | None = None) -> bytes:
        if uploaded_path or self.feed.file:
            return super().fetch(uploaded_path)

        timeout = int(self.config.get("timeout", 30))
        delay = float(self.config.get("request_delay", 0.4))
        retries = int(self.config.get("retries", 4))
        max_products = int(self.config.get("max_products") or 0)
        base_url = str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        skip_cats = set(self.config.get("skip_category_ids") or [2, 18, 22])

        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

        categories = self._categories(session, base_url, timeout, retries, skip_cats)
        variants = self._collect_variants(
            session, base_url, categories, timeout, retries, delay
        )
        if max_products:
            variants = variants[:max_products]
        if not variants:
            raise AdapterError("HoldCarp: nerasta nė vieno produkto.")

        # Iki unikalių produktų (id_product) – vienas puslapis per produktą.
        products: dict[str, list[dict[str, Any]]] = {}
        for variant in variants:
            products.setdefault(variant["id_product"], []).append(variant)

        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for index, (id_product, combos) in enumerate(products.items(), start=1):
            try:
                items.extend(
                    self._product_items(
                        session, base_url, id_product, combos, timeout, retries
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append({"id_product": id_product, "error": str(exc)[:300]})
                logger.warning("HoldCarp produkto klaida %s: %s", id_product, exc)
            if delay:
                time.sleep(delay)
            if index % 25 == 0:
                logger.info("HoldCarp: %s/%s produktų, %s eilučių", index, len(products), len(items))

        return json.dumps({"items": items, "errors": errors}, ensure_ascii=False).encode("utf-8")

    # ---------------------------------------------------------------- helpers

    def _get(self, session, url: str, timeout: int, retries: int, **post) -> Any:
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                if post:
                    response = session.post(
                        url, data=post, timeout=timeout,
                        headers={"X-Requested-With": "XMLHttpRequest"},
                    )
                else:
                    response = session.get(url, timeout=timeout)
                if response.status_code == 200:
                    return response
                last_exc = AdapterError(f"HTTP {response.status_code}")
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
        raise last_exc or AdapterError(f"užklausa nepavyko: {url}")

    def _categories(
        self, session, base_url: str, timeout: int, retries: int, skip: set[int]
    ) -> list[tuple[int, str]]:
        response = self._get(session, base_url + "/", timeout, retries)
        tree = lxml_html.fromstring(response.text)
        out: list[tuple[int, str]] = []
        for href in tree.xpath("//a/@href"):
            url = urllib.parse.urljoin(base_url + "/", href.split("?")[0])
            m = _CATEGORY_URL_RE.search(url)
            if m and int(m.group(1)) not in skip:
                pair = (int(m.group(1)), m.group(2).replace("-", " ").title())
                if pair[0] not in {c[0] for c in out}:
                    out.append(pair)
        return out

    def _collect_variants(
        self, session, base_url: str, categories, timeout, retries, delay
    ) -> list[dict[str, Any]]:
        """Kategorijų puslapiai -> variantų nuorodos (id_product, id_attr, ean)."""
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for cat_id, cat_name in categories:
            # PrestaShop kategoriją rezoliuoja pagal id – slug gali būti bet kas
            url = f"{base_url}/{cat_id}-x?resultsPerPage={MAX_PRODUCTS_PER_CATEGORY}"
            response = self._get(session, url, timeout, retries)
            tree = lxml_html.fromstring(response.text)
            real_cat = self._category_name(response.text) or cat_name
            for href in tree.xpath("//a/@href"):
                href = urllib.parse.urljoin(base_url + "/", href)
                m = _PRODUCT_URL_RE.search(href)
                if not m or "/gb/" not in href:
                    continue
                id_product, id_attr = m.group(1), m.group(2) or "0"
                ean = m.group(4) or ""
                fragment = m.group(5) or ""
                key = (id_product, id_attr)
                if key in out:
                    continue
                out[key] = {
                    "id_product": id_product,
                    "id_product_attribute": id_attr,
                    "ean": ean,
                    "category": real_cat,
                    "product_url": href.split("#")[0],
                    "fragment": fragment,
                }
            if delay:
                time.sleep(delay)
        return list(out.values())

    @staticmethod
    def _category_name(text: str) -> str:
        crumbs = _page_breadcrumbs(text)
        return crumbs[-1] if crumbs else ""

    # ------------------------------------------------------------- per produktas

    def _product_items(
        self, session, base_url: str, id_product: str,
        combos: list[dict[str, Any]], timeout: int, retries: int,
    ) -> list[dict[str, Any]]:
        first = combos[0]
        response = self._get(session, first["product_url"], timeout, retries)
        page = response.text
        tree = lxml_html.fromstring(page)
        product = _decode_data_product(page)

        name = _clean(tree.xpath("string(//h1)")) or str(product.get("name") or "")
        description = str(product.get("description") or "")
        crumbs = _page_breadcrumbs(page)
        if crumbs:
            crumbs = crumbs[:-1] if len(crumbs) > 1 else crumbs
        crumbs = crumbs or [first["category"]]
        images = self._images(tree, base_url)

        out: list[dict[str, Any]] = []
        for combo in combos:
            id_attr = combo["id_product_attribute"]
            combo_data = product
            combo_html = page
            if id_attr != "0":
                combo_data = self._combo_data(
                    session, first["product_url"], id_product, id_attr,
                    timeout, retries,
                ) or product
            out.append(
                self._make_item(
                    combo, combo_data, name, description, crumbs, images, combo_html
                )
            )
        return out

    def _combo_data(
        self, session, url: str, id_product: str, id_attr: str,
        timeout: int, retries: int,
    ) -> dict[str, Any] | None:
        response = self._get(
            session, url, timeout, retries,
            ajax="1", action="refresh", id_product=id_product,
            id_product_attribute=id_attr,
        )
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            return None
        details = str(data.get("product_details") or "")
        decoded = _decode_data_product(details)
        if not decoded:
            return None
        if decoded is not None:
            decoded["_preorder"] = "schema.org/PreOrder" in details
        return decoded

    def _make_item(
        self, combo, product: dict[str, Any], name: str, description: str,
        crumbs: list[str], images: list[str], combo_html: str,
    ) -> dict[str, Any]:
        attrs = product.get("attributes") or {}
        axes_raw: dict[str, str] = {}
        combo_ref = ""
        combo_ean = ""
        for attr in attrs.values():
            group = _clean(str(attr.get("group") or "")).lower()
            value = _clean(str(attr.get("name") or ""))
            if value:
                axes_raw[group] = value
            combo_ref = str(attr.get("reference") or "") or combo_ref
            combo_ean = str(attr.get("ean13") or "") or combo_ean

        reference = combo_ref or str(product.get("reference") or "")
        if not combo_ref and combo["id_product_attribute"] != "0":
            # Kombinacija be savo kodo – bendras produkto kodas sutrauktų variantus.
            reference = f"{reference or combo['id_product']}-{combo['id_product_attribute']}"
        external_id = reference or (
            f"{combo['id_product']}-{combo['id_product_attribute']}"
            if combo["id_product_attribute"] != "0" else str(product.get("id") or "")
        )
        quantity = self._to_int(product.get("quantity"))
        price_raw = str(product.get("price") or "")
        m = _EUR_PRICE_RE.search(price_raw.replace("€", ""))
        price = self._to_decimal(m.group(1)) if m else None
        preorder = bool(product.get("_preorder")) or "schema.org/PreOrder" in combo_html

        return {
            "external_id": external_id,
            "id_product": combo["id_product"],
            "id_product_attribute": combo["id_product_attribute"],
            "reference": reference,
            "name": name,
            "base_name": name,
            "axes_raw": axes_raw,
            "fragment_attrs": _fragment_attrs(combo.get("fragment") or ""),
            "product_group": combo["id_product"],
            "ean": combo_ean or combo["ean"],
            "quantity": quantity,
            "availability": str(product.get("availability") or ""),
            "preorder": preorder,
            "price": str(price) if price is not None else "",
            "category_path": crumbs,
            "description_html": description,
            "description": _html_to_text(description),
            "images": images,
            "source_url": combo["product_url"] + (combo.get("fragment") or ""),
        }

    @staticmethod
    def _breadcrumbs(text: str) -> list[str]:
        return _page_breadcrumbs(text)

    @staticmethod
    def _images(tree, base_url: str) -> list[str]:
        imgs: list[str] = []
        for src in tree.xpath(
            '//div[contains(@class,"product-images") or contains(@class,"images-container")]//@data-src'
            '|//div[contains(@class,"product-images") or contains(@class,"images-container")]//img/@src'
        ):
            if src and src.startswith("http"):
                imgs.append(src)
        return list(dict.fromkeys(imgs))

    # ------------------------------------------------------------------- parse

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        data = json.loads(payload.decode("utf-8"))
        if isinstance(data, str):
            data = json.loads(data)
        self.fetch_errors = len(data.get("errors") or [])
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
            raise AdapterError(f"HoldCarp: trūksta external_id: {repr(row)[:200]}")

        name = _clean(row.get("name") or "") or external_id
        crumbs = [c for c in (row.get("category_path") or []) if c]
        axes: dict[str, str] = {}
        for group, value in (row.get("axes_raw") or {}).items():
            axis = _AXIS_MAP.get(group.lower(), "variantas")
            if value:
                axes[axis] = value
        for group, value in (row.get("fragment_attrs") or {}).items():
            axis = _AXIS_MAP.get(group.lower(), "variantas")
            if value:
                axes.setdefault(axis, value)

        quantity = self._to_int(row.get("quantity"))
        preorder = bool(row.get("preorder"))
        in_stock_qty = int(self.config.get("in_stock_qty", DEFAULT_IN_STOCK_QTY))
        if quantity > 0:
            stock = quantity
        elif preorder:
            stock = in_stock_qty
        else:
            stock = 0
        is_available = stock > 0 or preorder

        rrp = self._to_decimal(row.get("price"))
        images = [u for u in (row.get("images") or []) if u]
        extra = {
            "identifiers": {
                "id_product": row.get("id_product"),
                "id_product_attribute": row.get("id_product_attribute"),
            },
            "base_name": row.get("base_name") or name,
            "product_group": row.get("product_group"),
            "sub_category_raw": crumbs[-1] if crumbs else "",
            "family_raw": crumbs[-2] if len(crumbs) > 1 else "",
            "sku_raw": row.get("reference"),
            "rrp_price": str(rrp) if rrp is not None else None,
            "stock_text": "preorder" if preorder else ("in_stock" if quantity > 0 else "out_of_stock"),
            "source_url": row.get("source_url"),
            "main_image_url": images[0] if images else "",
            "quantity_raw": quantity,
        }
        return NormalizedItem(
            external_id=external_id,
            sku=(row.get("reference") or "").strip() or external_id,
            name=name,
            ean=(row.get("ean") or "").strip(),
            brand_name=str(self.config.get("brand") or "HoldCarp"),
            category_path=" > ".join(crumbs),
            description=(row.get("description") or "").strip(),
            attributes={"axes": axes} if axes else {},
            images=images,
            stock=stock,
            purchase_price=None,  # pirkimo kainų nėra – public retail -> rrp
            currency="EUR",
            is_available=is_available,
            extra={k: v for k, v in extra.items() if v not in (None, "", {})},
            raw=row,
        )


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
