"""zuklys.lt (LT e-parduotuvė su Albatros prekėmis) -> ManufacturerContent(source="zuklys", language="lt").

Atsarginis šaltinis kodams be gamintojo turinio: paieška pagal Albatros kodą, prekės
pavadinimas prasideda kodu. Imama nuotrauka ir parametrų lentelė (aprašymo dažniausiai nėra).
Vartotojas leido naudoti šį turinį.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from urllib.parse import quote, urlsplit

from lxml import html as lxml_html

from apps.suppliers.manufacturers import http

SOURCE = "zuklys"
LANGUAGE = "lt"
SEARCH = "https://zuklys.lt/index.php?route=product/search&search={}"
_RESULT = re.compile(r'class="name name-product"[^>]*>\s*<a href="([^"]+)"')


def _clean(text: str) -> str:
    return " ".join((text or "").split())


HOSTS = {"zuklys.lt", "www.zuklys.lt"}


def _product_url(href: str) -> str | None:
    """Tik zuklys.lt nuorodos (ne svetimi/vidiniai adresai), be paieškos parametrų."""
    parts = urlsplit(html_lib.unescape(href))
    if parts.scheme not in ("http", "https") or parts.hostname not in HOSTS:
        return None
    return f"https://{parts.hostname}{parts.path}"


def search_urls(text: str, limit: int = 3) -> list[str]:
    urls = (_product_url(u) for u in _RESULT.findall(text))
    return list(dict.fromkeys(u for u in urls if u))[:limit]


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
    """Kiekvienam kodui: paieška -> iki 3 rezultatų puslapių, kol randamas kodas."""
    s = s or http.session()
    kwargs = {"sleep": sleep} if sleep else {}
    for n, code in enumerate(codes):
        if limit and n >= limit:
            return False
        response = http.get(s, SEARCH.format(quote(code)), delay, **kwargs)
        if response is None or response.status_code != 200:
            continue
        for url in search_urls(response.text):
            page = http.get(s, url, delay, **kwargs)
            if page is None or page.status_code != 200:
                continue
            parsed = parse_page(page.content, code)
            if parsed:
                yield LANGUAGE, url, parsed
                break
    return True
