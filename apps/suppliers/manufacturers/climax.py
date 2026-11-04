"""climax-fishingline.de serijų puslapiai -> ManufacturerContent.

Puslapiuose nėra prekių kodų: `codes` priskiriami pagal serijos pavadinimą
(Climax prekių base_name / sub_category_raw turi serijos žodžius).
"""

from __future__ import annotations

import re
import time

import requests
from lxml import html as lxml_html

BASE = "https://www.climax-fishingline.de/"
SITEMAPS = (BASE + "post-sitemap1.xml", BASE + "post-sitemap2.xml")
OVERVIEW_SITEMAP = BASE + "page-sitemap.xml"
_LOCALIZED = re.compile(r"/(en|fr)/")
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
            tree.xpath("//link[@hreflang]/@hreflang"),
            tree.xpath("//link[@hreflang]/@href"),
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
        if len(_clean(p.text_content())) >= 40
        and not _clean(p.text_content()).startswith("Ø")
    ]
    images = [
        src
        for src in root.xpath(".//img/@src")
        if "/wp-content/uploads/" in src and not re.search(r"-\d+x\d+\.", src)
    ]
    return {
        "series": series,
        "intro": "\n".join(dict.fromkeys(paragraphs)),
        "images": list(dict.fromkeys(images)),
        "languages": languages,
    }


def _locs(session: requests.Session, sitemap: str) -> list[str]:
    response = session.get(sitemap, timeout=30)
    return (
        re.findall(r"<loc>([^<]+)</loc>", response.text)
        if response.status_code == 200
        else []
    )


def discover_urls(session: requests.Session, delay: float = 0.5) -> list[str]:
    """Serijų puslapiai: post sitemap'ai + nuorodos iš apžvalgos puslapių (cult-carp/, ...)."""
    urls = [u for sitemap in SITEMAPS for u in _locs(session, sitemap)]
    for overview in _locs(session, OVERVIEW_SITEMAP):
        if _LOCALIZED.search(overview) or overview.rstrip("/") == BASE.rstrip("/"):
            continue
        response = session.get(overview, timeout=30)
        time.sleep(delay)
        if response.status_code != 200:
            continue
        tree = lxml_html.fromstring(response.content.decode("utf-8", errors="replace"))
        for href in tree.xpath("//main//a/@href"):
            href = href.split("#")[0]
            if (
                href.startswith(BASE)
                and href.count("/") == 4
                and "/wp-content/" not in href
            ):
                urls.append(href)
    return [u for u in dict.fromkeys(urls) if not _LOCALIZED.search(u)]


def crawl(
    languages: tuple[str, ...] = ("de", "en"), delay: float = 0.7, limit: int = 0
):
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    urls = discover_urls(session)
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
                page = parse_page(localized.content)
                page["series_de"] = parsed["series"]
                yield lang, href, page
