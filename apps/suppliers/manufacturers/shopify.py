"""Gamintojų Shopify parduotuvės (DAM grupė) -> ManufacturerContent.

`/products.json` grąžina visas prekes su variantais: Shopify prekė = bazinė prekė,
variant.sku = Albatros/DAM prekės kodas. Svetainėse yra tik dabartinio sezono prekės.
"""

from __future__ import annotations

from lxml import html as lxml_html

from apps.suppliers.manufacturers import http

SITES = {
    "savage-gear": "https://www.savagegear.com",
    "dam": "https://www.dam.de",
    "prologic": "https://www.prologicfishing.com",
    "madcat": "https://www.madcat-fishing.com",
}
LANGUAGE = "en"
SKU_PREFIX = "SVS"
PAGE_SIZE = 250


def _text(body_html: str) -> str:
    if not (body_html or "").strip():
        return ""
    tree = lxml_html.fromstring(f"<div>{body_html}</div>")
    for br in tree.iter("br"):
        br.tail = "\n" + (br.tail or "")
    for el in tree.iter("p", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
        el.tail = "\n" + (el.tail or "")
    lines = (" ".join(line.split()) for line in tree.text_content().splitlines())
    return "\n".join(line for line in lines if line)


def albatros_code(sku) -> str:
    """DAM grupės SKU `SVS65045` = Albatros kodas `65045`."""
    code = str(sku or "").strip().upper()
    if (
        code.startswith(SKU_PREFIX)
        and code[len(SKU_PREFIX) : len(SKU_PREFIX) + 1].isdigit()
    ):
        return code[len(SKU_PREFIX) :]
    return code


def parse_product(product: dict, base: str) -> dict:
    options = [o.get("name") or "" for o in product.get("options") or []]
    rows: dict[str, dict[str, str]] = {}
    for variant in product.get("variants") or []:
        code = albatros_code(variant.get("sku"))
        if not code:
            continue
        row = {}
        for index, name in enumerate(options, start=1):
            value = (variant.get(f"option{index}") or "").strip()
            if name and name != "Title" and value and value != "Default Title":
                row[name] = value
        rows[code] = row
    title = " ".join((product.get("title") or "").split())
    images = list(
        dict.fromkeys(
            i["src"].split("?")[0] for i in product.get("images") or [] if i.get("src")
        )
    )
    return {
        "series": title,
        "intro": _text(product.get("body_html") or ""),
        "codes": sorted(rows),
        "images": images,
        "sections": [{"heading": title, "text": "", "rows": rows}],
        "category": product.get("product_type") or "",
    }


def crawl(base: str, delay: float = http.MIN_DELAY, limit: int = 0, s=None, sleep=None):
    """Generuoja (language, url, parsed) kiekvienai prekei su bent vienu kodu.

    Grąžina True tik pilnai nuskaičius katalogą (tada galima nuimti dingusių prekių kodus)."""
    s = s or http.session()
    kwargs = {"sleep": sleep} if sleep else {}
    page, seen = 1, 0
    while True:
        response = http.get(
            s,
            f"{base}/products.json",
            delay,
            params={"limit": PAGE_SIZE, "page": page},
            **kwargs,
        )
        if response is None or response.status_code != 200:
            return False
        try:
            products = response.json()["products"]
        except (ValueError, KeyError, TypeError):
            return False
        if not isinstance(products, list) or (not products and page == 1):
            return False
        if not products:
            return True
        for product in products:
            parsed = parse_product(product, base)
            if not parsed["codes"]:
                continue
            yield LANGUAGE, f"{base}/products/{product['handle']}", parsed
            seen += 1
            if limit and seen >= limit:
                return False
        page += 1
