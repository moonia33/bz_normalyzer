"""nemus.lt (vienintelė Jobe / Humminbird / Minn Kota / Borika tiekėjo el. parduotuvė) ->
ManufacturerContent(source="nemus", language="lt").

Sitemap.xml -> visi prekių puslapiai (robots.txt leidžia; paieška ir rūšiavimas uždrausti).
Prekės kodas = JSON-LD `model` (sutampa su Albatros kodu). Imama nuotraukos ir LT aprašymas.
Vartotojas leido naudoti šį turinį.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from urllib.parse import urlsplit

from lxml import etree
from lxml import html as lxml_html

from apps.suppliers.manufacturers import http

SOURCE = "nemus"
LANGUAGE = "lt"
SITEMAP = "https://nemus.lt/sitemap.xml"
HOSTS = {"nemus.lt", "www.nemus.lt"}
MIN_DELAY = 2.5
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def sitemap_urls(xml: str) -> list[str]:
    urls = []
    for loc in _LOC.findall(xml):
        url = html_lib.unescape(loc)
        parts = urlsplit(url)
        if parts.scheme in ("http", "https") and parts.hostname in HOSTS:
            urls.append(url)
    return list(dict.fromkeys(urls))


def _product_ld(text: str) -> dict:
    for script in re.findall(
        r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', text, re.DOTALL
    ):
        try:
            data = json.loads(script)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return {}


def parse_page(content: bytes) -> dict | None:
    """Prekės puslapis -> turinys vienam kodui; be kodo (model) -> None."""
    text = content.decode("utf-8", errors="replace")
    ld = _product_ld(text)
    try:
        tree = lxml_html.fromstring(text)
    except etree.ParserError:
        return None
    code = _clean(ld.get("model") or tree.xpath('string(//li[@class="product-model"]/span)'))
    code = code.upper()
    if not code:
        return None
    name = _clean(html_lib.unescape(ld.get("name") or tree.xpath("string(//h1)")))
    tab = tree.xpath(
        '//ul[contains(@class,"nav-tabs")]//a[normalize-space()="Aprašymas"]/@href'
    )
    description = ""
    if tab and tab[0].startswith("#"):
        blocks = tree.xpath(f'//div[@id="{tab[0][1:]}"]//div[contains(@class,"block-content")]')
        if blocks:
            description = "\n".join(
                line for line in (_clean(t) for t in blocks[0].itertext()) if line
            )
    images = tree.xpath('//div[contains(@class,"swiper-slide")]/img/@data-largeimg')
    images = [u for u in dict.fromkeys(images) if urlsplit(u).hostname in HOSTS]
    return {
        "series": name,
        "intro": description,
        "codes": [code],
        "images": images,
        "sections": [{"heading": name, "text": "", "rows": {code: {}}}],
    }


def crawl(codes, delay: float = MIN_DELAY, limit: int = 0, s=None, sleep=None):
    """Visi sitemap puslapiai; grąžinami tik tie, kurių kodas yra `codes` (tuščias -> nieko)."""
    wanted = {str(code).strip().upper() for code in codes} - {""}
    if not wanted:
        return False
    delay = max(delay, MIN_DELAY)
    s = s or http.session()
    # Svetainė nutraukia keep-alive ryšius, kai užklausos dažnos.
    s.headers["Connection"] = "close"
    kwargs = {"sleep": sleep} if sleep else {}
    response = http.get(s, SITEMAP, delay, **kwargs)
    if response is None or response.status_code != 200:
        return False
    for n, url in enumerate(sitemap_urls(response.text)):
        if limit and n >= limit:
            return False
        page = http.get(s, url, delay, **kwargs)
        if page is None or page.status_code != 200:
            continue
        parsed = parse_page(page.content)
        if parsed and parsed["codes"][0] in wanted:
            yield LANGUAGE, url, parsed
    return True
