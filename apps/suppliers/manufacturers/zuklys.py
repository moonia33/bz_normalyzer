"""zuklys.lt (LT e-parduotuvė su Albatros prekėmis) -> ManufacturerContent(source="zuklys", language="lt").

Atsarginis šaltinis kodams be gamintojo turinio: kodas ieškomas sitemap.xml prekių URL
(paieška robots.txt uždrausta), puslapis priimamas tik jei prekės pavadinimas prasideda kodu. Imama nuotrauka ir parametrų lentelė (aprašymo dažniausiai nėra).
Vartotojas leido naudoti šį turinį.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from urllib.parse import urlsplit

from lxml import html as lxml_html

from apps.suppliers.manufacturers import http

SOURCE = "zuklys"
LANGUAGE = "lt"
SITEMAP = "https://zuklys.lt/sitemap.xml"
HOSTS = {"zuklys.lt", "www.zuklys.lt"}
MAX_CANDIDATES = 3
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _page_slug(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html").removesuffix("-lt")


def sitemap_index(xml: str) -> dict[str, list[tuple[str, str]]]:
    """Žodis iš prekės URL slug'o -> [(slug, url)]; tik zuklys.lt adresai."""
    index: dict[str, list[tuple[str, str]]] = {}
    for loc in _LOC.findall(xml):
        url = html_lib.unescape(loc)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or parts.hostname not in HOSTS:
            continue
        slug = _page_slug(url)
        for token in set(slug.split("-")):
            index.setdefault(token, []).append((slug, url))
    return index


def candidates(code: str, index: dict[str, list[tuple[str, str]]]) -> list[str]:
    """Prekių URL, kurių slug'e kodas yra atskira dalis (pradžioje, viduryje ar gale)."""
    code_slug = _slug(code)
    if not code_slug:
        return []
    urls = [
        url
        for slug, url in index.get(code_slug.split("-")[0], [])
        if slug == code_slug
        or slug.startswith(code_slug + "-")
        or slug.endswith("-" + code_slug)
        or f"-{code_slug}-" in slug
    ]
    return list(dict.fromkeys(urls))[:MAX_CANDIDATES]


def _product_ld(content: str) -> dict:
    for script in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', content, re.DOTALL
    ):
        try:
            data = json.loads(script)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            return data
    return {}


def parse_page(content: bytes, code: str) -> dict | None:
    """Puslapis priimamas tik jei prekės pavadinimas prasideda šiuo kodu."""
    text = content.decode("utf-8", errors="replace")
    ld = _product_ld(text)
    name = _clean(html_lib.unescape(ld.get("name") or ""))
    code = code.strip().upper()
    if not name.upper().startswith(code + " "):
        return None
    tree = lxml_html.fromstring(text)
    specs: dict[str, str] = {}
    for tr in tree.xpath('//div[@id="tab-specification"]//tr'):
        cells = [_clean(td.text_content()) for td in tr.xpath("./td")]
        if len(cells) == 2 and cells[0] and cells[1] and cells[0] not in specs:
            specs[cells[0]] = cells[1]
    description = "\n".join(
        line
        for line in (
            _clean(p) for p in tree.xpath('string(//div[@id="tab-description"])').splitlines()
        )
        if line
    )
    images = ld.get("image") or []
    images = [images] if isinstance(images, str) else list(images)
    images += tree.xpath('//a[contains(@class,"thumbnail")]/@href')
    images = [u for u in dict.fromkeys(images) if "/image/" in u]
    series = _clean(re.sub(re.escape(code), "", name, flags=re.IGNORECASE))
    return {
        "series": series,
        "intro": description,
        "codes": [code],
        "images": images,
        "sections": [{"heading": series, "text": "", "rows": {code: specs}}],
    }


def crawl(codes, delay: float = http.MIN_DELAY, limit: int = 0, s=None, sleep=None):
    """Sitemap (robots.txt draudžia paiešką) -> kiekvienam kodui iki 3 puslapių, kol randamas kodas."""
    s = s or http.session()
    kwargs = {"sleep": sleep} if sleep else {}
    response = http.get(s, SITEMAP, delay, **kwargs)
    if response is None or response.status_code != 200:
        return False
    index = sitemap_index(response.text)
    for n, code in enumerate(codes):
        if limit and n >= limit:
            return False
        for url in candidates(code, index):
            page = http.get(s, url, delay, **kwargs)
            if page is None or page.status_code != 200:
                continue
            parsed = parse_page(page.content, code)
            if parsed:
                yield LANGUAGE, url, parsed
                break
    return True
