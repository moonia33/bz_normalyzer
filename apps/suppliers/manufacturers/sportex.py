"""sportex.de serijų puslapiai -> ManufacturerContent (de + hreflang kalbos)."""

from __future__ import annotations

import re
import time

import requests
from lxml import html as lxml_html

SITEMAP = "https://www.sportex.de/wp-sitemap-posts-post-1.xml"
USER_AGENT = "Mozilla/5.0 (compatible; bz-normalyzer/1.0)"
_CODE_HEADER = re.compile(r"^(art|nr\.?\s*art|item|artikel)", re.IGNORECASE)
_SKIP_HEADER = re.compile(r"(uvp|rrp|preis|price|cena)", re.IGNORECASE)
_TEXT_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "table"}


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def parse_page(content: bytes) -> dict:
    """HTML -> {series, intro, sections, codes, images, languages}."""
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
    for bad in root.xpath(".//script|.//style|.//noscript|.//nav|.//footer|.//header"):
        bad.getparent().remove(bad)

    # Puslapio eiga: h5 serija + įžanga, h5 modeliai + aprašymai, h3 savybių /
    # video blokai (praleidžiami), h4 lentelės antraštė + lentelė.
    intro: list[str] = []
    blocks: list[tuple[str, str]] = []
    sections: list[dict] = []
    mode, heading, paragraphs = "intro", "", []

    def close_block():
        if mode == "block" and heading and paragraphs:
            blocks.append((heading, "\n".join(paragraphs)))

    for node in root.iter():
        tag = node.tag if isinstance(node.tag, str) else ""
        if tag not in _TEXT_TAGS or node.xpath("ancestor::table"):
            continue
        text = _clean(node.text_content())
        if tag in {"h1", "h6"}:
            continue
        if tag in {"h2", "h5"}:
            close_block()
            if text.lower() == series.lower():
                mode, heading, paragraphs = "intro", "", []
            else:
                mode, heading, paragraphs = "block", text, []
        elif tag == "h3":
            close_block()
            mode, heading, paragraphs = "skip", "", []
        elif tag == "h4":
            close_block()
            mode, heading, paragraphs = "table", text, []
        elif tag == "p" and len(text) >= 30:
            if mode == "intro":
                intro.append(text)
            elif mode == "block":
                paragraphs.append(text)
        elif tag == "table":
            rows = _table_rows(node)
            if rows:
                sections.append(
                    {
                        "heading": heading,
                        "text": _closest_block(heading, blocks),
                        "rows": rows,
                    }
                )
    close_block()
    codes = sorted({code for section in sections for code in section["rows"]})
    images = [
        src
        for src in root.xpath(".//img/@src")
        if "/wp-content/uploads/" in src and not re.search(r"-\d+x\d+\.", src)
    ]
    return {
        "series": series,
        "intro": "\n".join(dict.fromkeys(intro)),
        "sections": sections,
        "codes": codes,
        "images": list(dict.fromkeys(images)),
        "languages": languages,
    }


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _closest_block(heading: str, blocks: list[tuple[str, str]]) -> str:
    """Lentelės antraštė ('Rapid Feeder Light RS-2') -> aprašymo blokas ('… Light / XS')."""
    wanted = _tokens(heading)
    best, score = "", 0
    for title, text in blocks:
        overlap = len(wanted & _tokens(title))
        if overlap > score:
            best, score = text, overlap
    return best


def _table_rows(table) -> dict[str, dict[str, str]]:
    trs = table.xpath(".//tr")
    if len(trs) < 2:
        return {}
    header = [_clean(cell.text_content()) for cell in trs[0].xpath("./th|./td")]
    code_idx = next((i for i, h in enumerate(header) if _CODE_HEADER.match(h)), None)
    if code_idx is None:
        return {}
    rows: dict[str, dict[str, str]] = {}
    for tr in trs[1:]:
        cells = [_clean(cell.text_content()) for cell in tr.xpath("./th|./td")]
        if code_idx >= len(cells) or not cells[code_idx]:
            continue
        rows[cells[code_idx].upper()] = {
            header[i]: value
            for i, value in enumerate(cells)
            if i < len(header)
            and i != code_idx
            and value
            and not _SKIP_HEADER.search(header[i])
        }
    return rows


def series_urls(session: requests.Session) -> list[str]:
    response = session.get(SITEMAP, timeout=30)
    response.raise_for_status()
    return re.findall(r"<loc>([^<]+)</loc>", response.text)


def crawl(
    languages: tuple[str, ...] = ("de", "en", "pl"), delay: float = 0.7, limit: int = 0
):
    """Generuoja (language, url, parsed) – tik puslapiai su prekių lentelėmis."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    urls = series_urls(session)
    if limit:
        urls = urls[:limit]
    for url in urls:
        response = session.get(url, timeout=30)
        time.sleep(delay)
        if response.status_code != 200:
            continue
        parsed = parse_page(response.content)
        if not parsed["codes"]:
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
