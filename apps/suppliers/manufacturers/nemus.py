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
from urllib.parse import parse_qs, urlsplit

from lxml import etree
from lxml import html as lxml_html

from apps.suppliers.manufacturers import http

SOURCE = "nemus"
LANGUAGE = "lt"
SITEMAP = "https://nemus.lt/sitemap.xml"
HOSTS = {"nemus.lt", "www.nemus.lt"}
MIN_DELAY = 2.5
MAX_PAGES = 50
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


def _category(url: str) -> str:
    """'https://nemus.lt/laivai/pripuciami/x-12' -> 'laivai > pripuciami' (jei kelias turi skyrius)."""
    parts = [p for p in urlsplit(url).path.split("/") if p][:-1]
    return " > ".join(parts)


def parse_page(content: bytes, url: str = "") -> dict | None:
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
    offers = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}
    brand = ld.get("brand") if isinstance(ld.get("brand"), dict) else {}
    return {
        "series": name,
        "intro": description,
        "codes": [code],
        "images": images,
        "sections": [{"heading": name, "text": "", "rows": {code: {}}}],
        "category": _category(url),
        # Kaina su PVM (mažmeninė), prieinamumas – tik yra/nėra.
        "offer": {
            "price": str(offers.get("price") or ""),
            "in_stock": str(offers.get("availability") or "").endswith("/InStock"),
            "brand": _clean(brand.get("name") or ""),
        },
    }


def parse_category(content: bytes) -> tuple[str, list[str], int]:
    """Kategorijos puslapis -> ('Aksesuarai > Ram Laikikliai', prekių slug'ai, puslapių skaičius)."""
    try:
        tree = lxml_html.fromstring(content.decode("utf-8", errors="replace"))
    except etree.ParserError:
        return "", [], 0
    crumbs = [_clean(t) for t in tree.xpath('//ul[@class="breadcrumb"]/li/a/text()')]
    path = " > ".join(c for c in crumbs if c)
    links = tree.xpath('//div[contains(@class,"product-thumb")]//a/@href')
    slugs = [
        _slug(u) for u in links if urlsplit(u).hostname in HOSTS and "route=" not in u
    ]
    pages = [int(n) for n in re.findall(r"[?&]page=(\d+)", " ".join(tree.xpath("//a/@href")))]
    return path, list(dict.fromkeys(s for s in slugs if s)), min(max(pages, default=1), MAX_PAGES)


def _slug(url: str) -> str:
    """Prekės raktas: SEO slug arba 'product_id=N' (be slug'o: sitemap'e `?route=...&product_id=N`,
    kategorijoje `<kategorija>?product_id=N`)."""
    split = urlsplit(url)
    product_id = parse_qs(split.query).get("product_id")
    if product_id:
        return f"product_id={product_id[0]}"
    parts = [p for p in split.path.split("/") if p]
    return parts[-1] if parts else ""


def _add_category(categories: dict, path: str, slugs: list[str]) -> None:
    """Prekė keliose kategorijose -> giliausia (daugiausia lygių)."""
    for slug in slugs:
        if path.count(">") > categories.get(slug, "").count(">") or slug not in categories:
            categories[slug] = path


def category_of(url: str, categories: dict) -> str:
    return categories.get(_slug(url), "")


def crawl(delay: float = MIN_DELAY, limit: int = 0, s=None, sleep=None, report=None):
    """Visi sitemap prekių puslapiai (visas nemus katalogas).

    `report["complete"]` = True tik jei nuskaityti visi puslapiai; `report["urls"]` – sitemap'o URL
    (nuimami tik puslapiai, kurių nebėra sitemap'e, ne tie, kurių neišparsavo).
    Prekių URL sitemap'e be skyriaus, todėl `report["categories"]` = {slug: kelias} iš
    kategorijų puslapių (su puslapiavimu); jį pritaiko `category_of`."""
    report = report if report is not None else {}
    report["complete"] = False
    delay = max(delay, MIN_DELAY)
    s = s or http.session()
    # Svetainė nutraukia keep-alive ryšius, kai užklausos dažnos.
    s.headers["Connection"] = "close"
    kwargs = {"sleep": sleep} if sleep else {}
    response = http.get(s, SITEMAP, delay, **kwargs)
    if response is None or response.status_code != 200:
        return
    urls = sitemap_urls(response.text)
    if not urls:
        return
    report["urls"] = set(urls)
    categories = report["categories"] = {}
    failed = 0
    for n, url in enumerate(urls):
        if limit and n >= limit:
            return
        page = http.get(s, url, delay, **kwargs)
        if page is None or page.status_code != 200:
            failed += 1
            continue
        parsed = parse_page(page.content, url)
        if parsed:
            yield LANGUAGE, url, parsed
            continue
        failed += _crawl_category(s, url, page.content, categories, delay, kwargs)
    report["complete"] = failed == 0


def _crawl_category(s, url: str, content: bytes, categories: dict, delay: float, kwargs) -> int:
    path, slugs, pages = parse_category(content)
    if not path or not slugs:
        return 0
    _add_category(categories, path, slugs)
    failed = 0
    base = url.split("?", 1)[0]
    for number in range(2, pages + 1):
        page = http.get(s, f"{base}?page={number}", delay, **kwargs)
        if page is None or page.status_code != 200:
            failed += 1
            continue
        _add_category(categories, path, parse_category(page.content)[1])
    return failed
