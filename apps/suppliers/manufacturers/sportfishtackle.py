"""sportfishtackle.de (sportfiskeprylar.se DE versija) prekių puslapiai -> ManufacturerContent.

Mažmenininkas turi ir senesnių sezonų prekes, kurių gamintojų svetainėse nebėra.
Puslapis = bazinė prekė; JSON-LD `offers[].sku` = gamintojo prekės kodas (sutampa su
Albatros), `gtin12` = EAN. Prekių sąrašas – iš sportfiskeprylar.se sitemap (slug'ai
vienodi); sportfishtackle.com (EN) blokuoja Cloudflare, todėl imama DE.
"""

from __future__ import annotations

import html as html_lib
import json
import re

from lxml import html as lxml_html

from apps.suppliers.manufacturers import http

SITEMAP = "https://www.sportfiskeprylar.se/sitemap.xml"
BASE = "https://www.sportfishtackle.de/de/artiklar/"
LANGUAGE = "de"
SLUG_PREFIXES = {
    "savage-gear": ("savage-gear-",),
    "dam": ("dam-",),
    "prologic": ("prologic-",),
    "madcat": ("madcat-",),
    "mepps": ("mepps-",),
}
_SLUG = re.compile(r"/artiklar/([a-z0-9-]+)\.html")


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def product_urls(sitemap: str, brand: str) -> list[str]:
    prefixes = SLUG_PREFIXES[brand]
    slugs = (m.group(1) for m in _SLUG.finditer(sitemap))
    return [
        BASE + slug + ".html"
        for slug in dict.fromkeys(s for s in slugs if s.startswith(prefixes))
    ]


def _product_ld(tree) -> dict | None:
    for script in tree.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            data = json.loads(script)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return None


def _description(tree, ld: dict) -> str:
    for el in tree.xpath('//div[contains(@class,"product__text--long")]'):
        for br in el.iter("br"):
            br.tail = "\n" + (br.tail or "")
        for li in el.iter("li"):
            li.text = "• " + (li.text or "")
            li.tail = "\n" + (li.tail or "")
        lines = (_clean(line) for line in el.text_content().splitlines())
        text = "\n".join(line for line in lines if line)
        if text:
            return text
    return _clean(html_lib.unescape(ld.get("description") or ""))


def _specs(tree) -> list[tuple[str, str]]:
    out: dict[str, str] = {}
    for left in tree.xpath(
        '//td[contains(@class,"TeknSpec_") and contains(@class,"_Vanster")]'
    ):
        right = left.getnext()
        key = _clean(left.text_content()).rstrip(":").strip()
        if right is not None and key and key not in out:
            out[key] = _clean(right.text_content())
    return list(out.items())


def parse_page(content: bytes) -> dict:
    tree = lxml_html.fromstring(content.decode("utf-8", errors="replace"))
    ld = _product_ld(tree) or {}
    offers = ld.get("offers") or {}
    if isinstance(offers, dict):
        offers = offers.get("offers") or [offers]
    rows: dict[str, dict[str, str]] = {}
    eans: dict[str, str] = {}
    for offer in offers:
        code = str(offer.get("sku") or "").strip().upper()
        if not code:
            continue
        rows[code] = {"name": _clean(html_lib.unescape(offer.get("name") or ""))}
        ean = str(offer.get("gtin13") or offer.get("gtin12") or "").strip()
        if ean.isdigit():
            eans[code] = ean
    series = _clean(html_lib.unescape(ld.get("name") or "")) or _clean(
        tree.xpath("string(//h1)")
    )
    images = [
        re.sub(r"\?.*$", "", src)
        for src in tree.xpath('//a[contains(@href,"/bilder/artiklar/zoom/")]/@href')
        + tree.xpath('//img[contains(@src,"/bilder/artiklar/zoom/")]/@src')
        + re.findall(
            r'["\'](/bilder/artiklar/zoom/[^"\'?\s]+)',
            content.decode("utf-8", "replace"),
        )
    ]
    images = [
        src if src.startswith("http") else "https://www.sportfishtackle.de" + src
        for src in images
    ]
    specs = _specs(tree)
    return {
        "series": series,
        "intro": _description(tree, ld),
        "codes": sorted(rows),
        "images": list(dict.fromkeys(images)),
        "sections": [
            {
                "heading": series,
                "text": "\n".join(f"{k}: {v}" for k, v in specs),
                "rows": rows,
            }
        ],
        "eans": eans,
        "category": _clean(ld.get("category") or ""),
    }


def crawl(
    brand: str, delay: float = http.MIN_DELAY, limit: int = 0, s=None, sleep=None
):
    """Grąžina True, kai sitemap ir visi puslapiai nuskaityti (404 = prekė pašalinta;
    puslapis be kodų = neaišku, kodai nevalomi)."""
    s = s or http.session()
    kwargs = {"sleep": sleep} if sleep else {}
    response = http.get(s, SITEMAP, delay, **kwargs)
    if response is None or response.status_code != 200:
        return False
    urls = product_urls(response.text, brand)
    if not urls:
        return False
    complete = not limit or len(urls) <= limit
    if limit:
        urls = urls[:limit]
    for url in urls:
        page = http.get(s, url, delay, **kwargs)
        if page is None or page.status_code != 200:
            complete = complete and page is not None and page.status_code == 404
            continue
        parsed = parse_page(page.content)
        if parsed["codes"]:
            yield LANGUAGE, url, parsed
        else:
            complete = False
    return complete
