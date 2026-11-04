"""climax-fishingline.de serijų puslapiai -> ManufacturerContent.

Puslapiuose nėra prekių kodų: `codes` priskiriami pagal serijos pavadinimą
(Climax prekių base_name / sub_category_raw turi serijos žodžius).
"""

from __future__ import annotations

import re
import time

import requests
from lxml import html as lxml_html

SITEMAPS = (
    "https://www.climax-fishingline.de/post-sitemap1.xml",
    "https://www.climax-fishingline.de/post-sitemap2.xml",
)
USER_AGENT = "Mozilla/5.0 (compatible; bz-normalyzer/1.0)"


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 1}


def parse_page(content: bytes) -> dict:
    tree = lxml_html.fromstring(content.decode("utf-8", errors="replace"))
    languages = {
        lang: href
        for lang, href in zip(
            tree.xpath("//link[@hreflang]/@hreflang"), tree.xpath("//link[@hreflang]/@href")
        )
        if lang != "x-default"
    }
    series = _clean(tree.xpath("string(//h1)")) or _clean(
        (tree.xpath('//meta[@property="og:title"]/@content') or [""])[0]
    )
    root = (tree.xpath("//main") or tree.xpath("//article") or [tree])[0]
    paragraphs = [
        _clean(p.text_content())
        for p in root.xpath(".//p")
        if len(_clean(p.text_content())) >= 40 and not _clean(p.text_content()).startswith("Ø")
    ]
    images = [
        src for src in root.xpath(".//img/@src")
        if "/wp-content/uploads/" in src and not re.search(r"-\d+x\d+\.", src)
    ]
    return {
        "series": series,
        "intro": "\n".join(dict.fromkeys(paragraphs)),
        "images": list(dict.fromkeys(images)),
        "languages": languages,
    }


def crawl(languages: tuple[str, ...] = ("de", "en"), delay: float = 0.7, limit: int = 0):
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    urls: list[str] = []
    for sitemap in SITEMAPS:
        response = session.get(sitemap, timeout=30)
        if response.status_code == 200:
            urls += re.findall(r"<loc>([^<]+)</loc>", response.text)
    urls = [u for u in urls if not re.search(r"/(en|fr)/", u)]
    if limit:
        urls = urls[:limit]
    for url in urls:
        response = session.get(url, timeout=30)
        time.sleep(delay)
        if response.status_code != 200:
            continue
        parsed = parse_page(response.content)
        if not parsed["intro"]:
            continue
        if "de" in languages:
            yield "de", url, parsed
        for lang in languages:
            href = parsed["languages"].get(lang)
            if lang == "de" or not href or href == url:
                continue
            localized = session.get(href, timeout=30)
            time.sleep(delay)
            if localized.status_code == 200:
                yield lang, href, parse_page(localized.content)
