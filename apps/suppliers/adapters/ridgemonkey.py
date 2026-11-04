from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import requests
from lxml import html as lxml_html

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://b2b.ridgemonkey.co.uk"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

# /changelanguage/<id> – iš login puslapio kalbų meniu.
LANG_IDS = {
    "en": 1, "fr": 2, "it": 3, "nl": 4,
    "de": 5, "pl": 6, "cs": 7, "es": 8,
}
# 'In Stock' – tik boolean svetainėje; įvertis į qty_on_hand.
DEFAULT_IN_STOCK_QTY = 10
# Sesija viena – jei kas nors kitas prisijungia, viskas -> 302 į /login.
MAX_RELOGINS = 8

_PRICE_RE = re.compile(r"([\d][\d\s.,]*)\s*\(\s*EUR\s*\)", re.I)
_BARCODE_RE = re.compile(r"/barcode/(\d{6,})")
_CART_RE = re.compile(r"addproducttocart/details/(\d+)")
_MAIN_IMG_RE = re.compile(r"main-product-img-(\d+)")
# 'Foo Size 4 - Trade Pack of 5 (RMT286)' / '... - Single Item (RMC231)'
# ('Singe Item' – rašybos klaida, sutinkama tiekėjo duomenyse/Shopify)
_ROW_TAIL_RE = re.compile(
    r"^(?P<base>.*?)\s*-\s*"
    r"(?P<kind>Sing[le]+\s*Item|Trade\s*Pack\s*of\s*(?P<pack>\d+))\s*"
    r"\((?P<art>[A-Za-z0-9&\- ]+)\)\s*$",
    re.I,
)
_ART_ONLY_RE = re.compile(r"\(([A-Za-z0-9&\- ]+)\)\s*$")
_WS_RE = re.compile(r"\s+")

_SIZE_LETTERS = {"XS", "S", "M", "L", "XL", "XXL", "XXXL", "XXXXL", "4XL", "5XL"}
_COLOR_WORDS = {
    "green", "black", "camo", "carbon", "hi-vis", "hivis", "grey", "gray",
    "olive", "red", "blue", "white", "yellow", "orange", "pink", "purple",
    "silver", "gold", "brown", "khaki", "navy", "charcoal", "weed",
}
_SIZE_WORD_RE = re.compile(r"^size\s+(.+)$", re.I)
_MEAS_TAIL_RE = re.compile(
    r"^[\d.,]+\s*(mm|cm|m|g|kg|lb|lbs|oz|l|ml|ft|inch|pcs)$", re.I
)


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _is_login_page(text: str) -> bool:
    return "html-login-page" in text or (
        'name="Password"' in text and 'name="Email"' in text
    )


def _is_error_page(text: str) -> bool:
    return "<title>Error</title>" in text and "internal error" in text


def _classify_row(row_name: str) -> dict[str, Any]:
    """Eilutės pavadinimas -> {base_name, row_type, trade_pack_qty, art_no}."""
    m = _ROW_TAIL_RE.match(row_name)
    if m:
        kind = "single_item" if m.group("kind").lower().startswith("sing") else "trade_pack"
        return {
            "base_name": m.group("base").strip(),
            "row_type": kind,
            "trade_pack_qty": int(m.group("pack") or 1),
            "art_no": _WS_RE.sub("", m.group("art")),
        }
    m = _ART_ONLY_RE.search(row_name)
    return {
        "base_name": _ART_ONLY_RE.sub("", row_name).strip(),
        "row_type": "single_item",
        "trade_pack_qty": 1,
        "art_no": _WS_RE.sub("", m.group(1)) if m else "",
    }


def _common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    prefix = strings[0]
    for s in strings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    if prefix and not prefix.endswith(" "):
        prefix = prefix[: prefix.rfind(" ") + 1] if " " in prefix else ""
    return prefix


def _axes_from_label(label: str) -> dict[str, str]:
    """Varianto label'io ('4', 'S', 'Weed Green', '300m') -> option ašys."""
    label = (label or "").strip()
    if not label:
        return {}
    up = label.upper()
    if up in _SIZE_LETTERS or re.fullmatch(r"\d{1,3}", label):
        return {"size": label}
    m = _SIZE_WORD_RE.match(label)
    if m:
        return {"size": m.group(1)}
    low = label.lower()
    words = low.split()
    for n in range(len(words), 0, -1):
        tail = words[-n:]
        if " ".join(tail) in _COLOR_WORDS or all(w in _COLOR_WORDS for w in tail):
            return {"color": " ".join(tail).title()}
    if _MEAS_TAIL_RE.match(low):
        return {"variantas": label}
    return {"variantas": label}


@register_adapter
class RidgeMonkeyAdapter(BaseAdapter):
    """b2b.ridgemonkey.co.uk (nopCommerce) scraper'is.

    Tiekėjas feed'o neturi – visas katalogas scrap'inamas po B2B login'o.
    Katalogas iš /sitemap.xml (~264 grouped produktų puslapiai); eilutės –
    `product-variant-list` lentelė (Product | SKU | Barcode | Stock | Price).
    Kainos EUR be PVM; likutis tik boolean (In/Out of Stock).

    'Single Item' vs 'Trade Pack of N': parduodame po vieną – jei yra
    single eilučių, jos variantai (pack qty iš giminių eilučių); jei tik
    pack eilutės – kiekviena tampa vienetiniu variantu (cost = kaina/N).

    Auth: POST /login su __RequestVerificationToken; leidžiama VIENA sesija
    (kitas login išmeta – detektuojam pagal login-page markerį ir reloginam).
    Laikini 'internal error' puslapiai – retry su backoff.

    Kalbos: feed.language.code -> /changelanguage/<id>; kalbiniai feed'ai
    (pl/de) scrap'ina tuos pačius puslapius – aprašymai verčiami (OfferText),
    pavadinimai/variantai visada EN.

    config: username, password, base_url, request_delay, timeout, retries,
    max_products (testams), in_stock_qty (default 10).
    """

    code = "ridgemonkey"

    # ------------------------------------------------------------------ fetch

    def fetch(self, uploaded_path: str | None = None) -> bytes:
        if uploaded_path or self.feed.file:
            return super().fetch(uploaded_path)

        timeout = int(self.config.get("timeout", 30))
        delay = float(self.config.get("request_delay", 0.5))
        retries = int(self.config.get("retries", 4))
        max_products = int(self.config.get("max_products") or 0)
        base_url = str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        username = str(self.config.get("username") or "")
        password = str(self.config.get("password") or "")
        if not username or not password:
            raise AdapterError("RidgeMonkey: config turi turėti username ir password.")

        feed_lang = "en"
        language = getattr(self.feed, "language", None)
        if language is not None and getattr(language, "code", ""):
            feed_lang = str(language.code).lower()

        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        self._relogins = 0
        self._lang = feed_lang
        self._login(session, base_url, username, password, timeout)
        if feed_lang != "en":
            self._set_language(session, base_url, feed_lang, timeout, retries)

        urls = self._sitemap_urls(session, base_url, timeout, retries)
        if max_products:
            urls = urls[:max_products]

        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for index, url in enumerate(urls, start=1):
            try:
                text = self._get_text(session, url, base_url, timeout, retries)
                if not self._is_product_page(text):
                    continue
                items.extend(self._parse_product_page(text, url))
            except Exception as exc:  # noqa: BLE001 – vienas puslapis nestabdo
                errors.append({"url": url, "error": str(exc)[:300]})
                logger.warning("RidgeMonkey puslapio klaida %s: %s", url, exc)
            if delay:
                time.sleep(delay)
            if index % 25 == 0:
                logger.info("RidgeMonkey: %s/%s puslapių, %s eilučių", index, len(urls), len(items))

        if not items:
            raise AdapterError("RidgeMonkey: nerasta nė vieno produkto.")
        return json.dumps({"items": items, "errors": errors}, ensure_ascii=False).encode("utf-8")

    # ---------------------------------------------------------------- helpers

    def _login(self, session, base_url: str, username: str, password: str, timeout: int) -> None:
        page = session.get(f"{base_url}/login", timeout=timeout)
        page.raise_for_status()
        tree = lxml_html.fromstring(page.text)
        token = (
            tree.xpath('//input[@name="__RequestVerificationToken"]/@value') or [""]
        )[0]
        response = session.post(
            f"{base_url}/login?returnurl=%2F",
            data={
                "Email": username,
                "Password": password,
                "RememberMe": "true",
                "__RequestVerificationToken": token,
            },
            timeout=timeout,
            allow_redirects=True,
        )
        if _is_login_page(response.text):
            raise AdapterError("RidgeMonkey: prisijungimas nepavyko (patikrinti credentials).")
        # Default kalba būna fr – visada grąžinam į en prieš scraping.
        self._set_language(session, base_url, "en", timeout, 1)

    def _set_language(self, session, base_url: str, lang: str, timeout: int, retries: int) -> None:
        lang_id = LANG_IDS.get(lang)
        if not lang_id:
            return
        self._get_text(
            session, f"{base_url}/changelanguage/{lang_id}?returnUrl=%2F",
            base_url, timeout, retries, _relogin=False,
        )

    def _get_text(
        self, session, url: str, base_url: str, timeout: int, retries: int,
        *, _relogin: bool = True,
    ) -> str:
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = session.get(url, timeout=timeout)
                text = response.text
                if response.status_code != 200 or _is_error_page(text):
                    last_exc = AdapterError(f"HTTP {response.status_code}")
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if _relogin and _is_login_page(text):
                    self._relogins += 1
                    if self._relogins > MAX_RELOGINS:
                        raise AdapterError(
                            "Per daug relogin'ų – kažkas kitas naudoja B2B account'ą."
                        )
                    username = str(self.config.get("username") or "")
                    password = str(self.config.get("password") or "")
                    self._login(session, base_url, username, password, timeout)
                    if getattr(self, "_lang", "en") != "en":
                        self._set_language(session, base_url, self._lang, timeout, 1)
                    continue
                return text
            except AdapterError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        raise last_exc or AdapterError(f"užklausa nepavyko: {url}")

    def _sitemap_urls(self, session, base_url: str, timeout: int, retries: int) -> list[str]:
        text = self._get_text(session, f"{base_url}/sitemap.xml", base_url, timeout, retries)
        urls: list[str] = []
        seen: set[str] = set()
        for loc in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", text):
            url = re.sub(r"^http://", "https://", loc.strip())
            if not url or url.rstrip("/") in (base_url, f"{base_url}/login"):
                continue
            if url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    @staticmethod
    def _is_product_page(text: str) -> bool:
        return "product-variant-list" in text or "product-details-form" in text

    # --------------------------------------------------------------- page parse

    def _parse_product_page(self, text: str, url: str) -> list[dict[str, Any]]:
        tree = lxml_html.fromstring(text)

        h1 = _clean(tree.xpath("string(//h1)"))
        product_name = _ART_ONLY_RE.sub("", h1).strip()
        group_m = _MAIN_IMG_RE.search(text)
        group_id = group_m.group(1) if group_m else ""
        crumbs = [
            _clean(t) for t in tree.xpath(
                '//div[contains(@class,"breadcrumb")]//*[@itemprop="name"]//text()'
            ) if _clean(t)
        ]
        crumbs = [c for c in crumbs if c.lower() != "home"]
        if crumbs and crumbs[-1].lower() in {h1.lower(), product_name.lower()}:
            crumbs = crumbs[:-1]

        images: list[str] = []
        for src in tree.xpath("//@data-fullsize"):
            if src.strip():
                images.append(urllib.parse.urljoin(url, src.strip()))
        if not images:
            for src in tree.xpath(
                '//a[contains(@id,"main-product-img-lightbox-anchor")]/@href'
                '|//img[contains(@id,"main-product-img-")]/@src'
            ):
                if src.strip():
                    images.append(urllib.parse.urljoin(url, src.strip()))
        images = [u.replace("http://", "https://") for u in dict.fromkeys(images)]

        desc_nodes = tree.xpath('//div[contains(@class,"full-description")]')
        description = ""
        if desc_nodes:
            description = "".join(
                lxml_html.tostring(child, encoding="unicode")
                for child in desc_nodes[0]
            ).strip() or _clean(desc_nodes[0].text_content())

        rows = self._parse_variant_rows(tree)
        if not rows:
            return []
        for row in rows:
            row.update(_classify_row(row["row_name"]))
        singles = [r for r in rows if r["row_type"] == "single_item"]
        packs = [r for r in rows if r["row_type"] == "trade_pack"]
        if singles:
            sellable = [
                dict(r, unit_basis="unit", trade_pack_qty=next(
                    (p["trade_pack_qty"] for p in packs
                     if p["base_name"] == r["base_name"]), 0))
                for r in singles
            ]
        else:
            sellable = [dict(r, unit_basis="pack_share") for r in packs]

        prefix = _common_prefix([r["base_name"] for r in sellable]) if len(sellable) > 1 else ""
        page_slug = urllib.parse.urlparse(url).path.strip("/") or "home"

        out: list[dict[str, Any]] = []
        for row in sellable:
            variant_label = row["base_name"][len(prefix):].strip() if prefix else ""
            if not variant_label and len(sellable) > 1:
                variant_label = row["base_name"].strip()
            pack_price = row["price"]
            pack_qty = row["trade_pack_qty"]
            if row["unit_basis"] == "pack_share" and pack_price is not None and pack_qty:
                unit_price = (pack_price / pack_qty).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP)
            else:
                unit_price = pack_price
            out.append(
                {
                    "art_no": row["art_no"],
                    "supplier_sku": row["sku"],
                    "name": row["base_name"],
                    "base_name": product_name,
                    "variant_label": variant_label,
                    "product_group": page_slug,
                    "nop_product_id": group_id,
                    "nop_row_id": row["nop_row_id"],
                    "row_type": row["row_type"],
                    "unit_basis": row["unit_basis"],
                    "trade_pack_qty": pack_qty if row["row_type"] == "trade_pack" else row.get("trade_pack_qty") or 0,
                    "pack_price": str(pack_price) if (pack_price is not None and row["row_type"] == "trade_pack") else "",
                    "unit_price": str(unit_price) if unit_price is not None else "",
                    "ean": row["ean"],
                    "stock_status": row["stock_status"],
                    "category_path": crumbs,
                    "description": description,
                    "images": images,
                    "source_url": url,
                }
            )
        return out

    @staticmethod
    def _parse_variant_rows(tree) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for tr in tree.xpath(
            '//div[contains(@class,"product-variant-list")]//tr[td]'
        ):
            cells = tr.xpath("./td")
            if len(cells) < 5:
                continue
            name = _clean(cells[0].text_content())
            sku = _clean(cells[1].text_content())
            bc_html = lxml_html.tostring(cells[2], encoding="unicode")
            bc_m = _BARCODE_RE.search(bc_html)
            ean = bc_m.group(1) if bc_m else re.sub(r"\D", "", _clean(cells[2].text_content()))[:13]
            stock_html = lxml_html.tostring(cells[3], encoding="unicode")
            if "stock-True" in stock_html:
                stock_status = "in_stock"
            elif "stock-False" in stock_html or "out of stock" in _clean(cells[3].text_content()).lower():
                stock_status = "out_of_stock"
            else:
                stock_status = "in_stock"
            price_m = _PRICE_RE.search(cells[4].text_content() or "")
            price = None
            if price_m:
                try:
                    price = Decimal(price_m.group(1).replace(" ", "").replace(",", ""))
                except Exception:  # noqa: BLE001
                    price = None
            row_html = lxml_html.tostring(tr, encoding="unicode")
            cart_m = _CART_RE.search(row_html)
            rows.append(
                {
                    "row_name": name,
                    "sku": sku,
                    "ean": ean or "",
                    "stock_status": stock_status,
                    "price": price,
                    "nop_row_id": cart_m.group(1) if cart_m else "",
                }
            )
        return rows

    # ------------------------------------------------------------------- parse

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        data = json.loads(payload.decode("utf-8"))
        if isinstance(data, str):
            data = json.loads(data)
        seen: set[str] = set()
        for row in data.get("items") or []:
            art_no = (row.get("art_no") or "").strip()
            if not art_no:
                continue
            if art_no in seen:
                row["duplicate_art_no"] = True
                continue
            seen.add(art_no)
            yield row

    # --------------------------------------------------------------- normalize

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        art_no = (row.get("art_no") or "").strip()
        if not art_no:
            raise AdapterError(f"RidgeMonkey: trūksta art-no eilutėje: {repr(row)[:200]}")

        name = (row.get("name") or "").strip() or art_no
        crumbs = [c for c in (row.get("category_path") or []) if c]
        category_path = " > ".join(crumbs)
        axes = _axes_from_label(row.get("variant_label") or "")
        stock_status = (row.get("stock_status") or "").strip()
        in_stock_qty = int(self.config.get("in_stock_qty", DEFAULT_IN_STOCK_QTY))
        stock = in_stock_qty if stock_status == "in_stock" else 0
        price = self._to_decimal(row.get("unit_price"))
        pack_qty = self._to_int(row.get("trade_pack_qty"))
        pack_price = self._to_decimal(row.get("pack_price"))
        images = [u for u in (row.get("images") or []) if u]
        ean = (row.get("ean") or "").strip()
        # Pakuotės barkodas netinka vienetui – lieka tik extra.
        pack_ean = ean if row.get("unit_basis") == "pack_share" else ""
        if pack_ean:
            ean = ""

        extra = {
            "pack_ean": pack_ean,
            "identifiers": {"art_no": art_no, "nop_row_id": row.get("nop_row_id")},
            "supplier_sku": row.get("supplier_sku"),
            "base_name": row.get("base_name") or name,
            "product_group": row.get("product_group"),
            "sub_category_raw": crumbs[-1] if len(crumbs) > 1 else "",
            "family_raw": crumbs[-2] if len(crumbs) > 1 else "",
            "variant_label": row.get("variant_label"),
            "row_type": row.get("row_type"),
            "unit_basis": row.get("unit_basis"),
            "trade_pack_qty": pack_qty if pack_qty > 1 else None,
            "pack_price": str(pack_price) if pack_price is not None else None,
            "stock_text": row.get("stock_status"),
            "source_url": row.get("source_url"),
            "main_image_url": images[0] if images else "",
        }
        return NormalizedItem(
            external_id=art_no,
            sku=art_no,
            name=name,
            ean=ean,
            brand_name=str(self.config.get("brand") or "RidgeMonkey"),
            category_path=category_path,
            description=(row.get("description") or "").strip(),
            attributes={"axes": axes} if axes else {},
            images=images,
            stock=stock,
            purchase_price=price,
            currency="EUR",
            is_available=stock > 0,
            extra={k: v for k, v in extra.items() if v not in (None, "", {})},
            raw=self._clean_raw(row),
        )

    @staticmethod
    def _clean_raw(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): RidgeMonkeyAdapter._clean_raw(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [RidgeMonkeyAdapter._clean_raw(v) for v in value]
        if isinstance(value, Decimal):
            return str(value)
        return value
