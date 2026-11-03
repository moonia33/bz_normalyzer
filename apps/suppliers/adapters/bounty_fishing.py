from __future__ import annotations

import html
import json
import logging
import re
import time
from collections import defaultdict
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://bounty.fishing"
DEFAULT_LOGIN_URL = f"{DEFAULT_BASE_URL}/login/"
DEFAULT_CATALOG_PATH = "/catall/"

# Skonių žodynas (CAPS, ilgesni pirmi). Konfigūruojama per
# feed.config["flavor_vocabulary"] – naujas skonis = tik config pakeitimas.
DEFAULT_FLAVORS = [
    "SQUID / SALMON / BELACHAN / ROBIN RED",
    "KRILL / HALIBUT / BELACHAN",
    "KRILL / SQUID / BELACHAN",
    "COBRA SQUID / BLOODWORM",
    "SQUID / BLOODWORM",
    "SQUID / BLACK PEPPER",
    "SQUID / OCTOPUS",
    "SQUID / STRAWBERRY",
    "HALIBUT / TIGER NUT",
    "KRILL / ROBIN RED",
    "KRIL / ROBIN RED",
    "KRILL / CRANBERRY",
    "SHRIMP / KRILL",
    "SHRIMP KRILL",
    "STRAWBERRY / SALMON",
    "STRAWBERRY/SALMON",
    "MULBERRY / FLORENTINE",
    "CRAB / BANANA",
    "RED FISH / BLACKBERRY",
    "ANANAS BANANA",
    "COCONUT TIGER NUT",
    "CARAMEL / VANILLA",
    "SWEET CORN",
    "TROPICAL FRUITS",
    "WHITE CHOCOLATE",
    "MILKY CORN",
    "ACID PEAR",
    "ASIAN SPICES",
    "BLACK COFFEE",
    "TUTTI FRUTTI",
    "TUTTI-FRUTTI",
    "ATLANTIC TUNA",
    "MONSTER CRAB",
    "SWEET SPICES",
    "WILD STRAWBERRY",
    "BLACK SQUID",
    "KING SHRIMP",
    "RED HALIBUT",
    "TUTTI FRUTTI",
    "ESTERFRUIT",
    "N-BUTYRIC",
    "BLACKBERRY",
    "CRANBERRY",
    "BLOODWORM",
    "PINEAPPLE",
    "PINAPPLE",
    "MANDARIN",
    "RED FISH",
    "ROBIN RED",
    "HOT DEMON",
    "BELACHAN",
    "STRAWBERRY",
    "TIGER NUT",
    "HALIBUT",
    "GARLIC",
    "CHERRY",
    "SALMON",
    "SHRIMP",
    "SQUID",
    "KRILL",
    "CORN",
    "PLUM",
    "HONEY",
    "ANISE",
    "HEMP",
    "TEQUILA",
    "VANILLA",
    "BANANA",
    "TUNA",
    "CRAB",
    "GLM",
    "SCOPEX",
    "LIVER",
    "MAPLE",
    "OCTOPUS",
    "FISH",
    "NUT",
    "SPICE",
    "NATURAL",
]

# Žodžiai, ignoruojami bazėje (užpildai, keičiantys tik vardo formą).
DEFAULT_FILLER_WORDS = ["hookbait"]

# Pavadinimų normalizavimas (ru/ua/kalbų maišyti ir rašybos variantai).
DEFAULT_NAME_REPLACEMENTS = {
    "Преміум насадка": "Premium hookbait",
    "Ліквід": "Liquid",
    "Ликвид": "Liquid",
    "BOUNT ": "BOUNTY ",
}

_MEAS_PATTERNS = {
    "size": re.compile(
        r"\b\d+\s*/\s*\d+\s*mm\.?"
        r"|\b\d+(?:[.,]\d+)?\s*-\s*\d+(?:[.,]\d+)?\s*mm\.?"
        r"|\b\d+(?:[.,]\d+)?\s*mm\.?",
        re.I,
    ),
    "weight": re.compile(r"\b\d+(?:[.,]\d+)?\s*[кk]g\b", re.I),
    "volume": re.compile(
        r"\b\d+(?:[.,]\d+)?\s*л\b"
        r"|\b\d+(?:[.,]\d+)?\s*мл\b"
        r"|\b\d+(?:[.,]\d+)?\s*ml\b"
        r"|\b\d+(?:[.,]\d+)?\s*l\b",
        re.I,
    ),
    "pack": re.compile(r"\b\d+\s*pcs\.?|\b\d+\s*vnt\.?", re.I),
    "color": re.compile(r"\bMix-colors?\b", re.I),
}

_WORD_RE = re.compile(r"[^A-Za-zА-Яа-яІіЇїЄє0-9]+")


def _slug(text: str) -> str:
    text = _WORD_RE.sub("-", text.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", text)


def _flavor_regex(vocabulary: list[str]) -> re.Pattern | None:
    words = sorted({w.strip() for w in vocabulary if w.strip()}, key=len, reverse=True)
    if not words:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b")


def _apply_replacements(name: str, replacements: dict[str, str]) -> str:
    for src, dst in replacements.items():
        if src and src.lower() in name.lower():
            name = re.sub(re.escape(src), dst, name, flags=re.I)
    return name


def _collapse_duplicated(name: str) -> str:
    """'X X' pakartojimas -> 'X' ('Boiled bait boilies KRILL Boiled bait
    boilies KRILL' -> 'Boiled bait boilies KRILL')."""
    words = name.split()
    n = len(words)
    for k in range(1, n // 2 + 1):
        if words[:k] == words[k : 2 * k]:
            return " ".join(words[k:])
    return name


def _common_prefix(codes: list[str]) -> str:
    """Bendras kodų prefiksas po metų (25/26) nupjovimo ('26WFCB1' -> 'WFCB')."""
    stripped = [re.sub(r"^\d{2}(?=[A-Za-z])", "", c) for c in codes]
    if not stripped:
        return ""
    prefix = stripped[0]
    for code in stripped[1:]:
        while prefix and not code.startswith(prefix):
            prefix = prefix[:-1]
    return prefix


def _original_image(url: str) -> str:
    """image/cache/...-500x500.jpg -> image/...jpg (pilna raiska)."""
    url = re.sub(r"/image/cache/", "/image/", url)
    return re.sub(r"-\d+x\d+(\.\w+)$", r"\1", url)


@register_adapter
class BountyFishingAdapter(BaseAdapter):
    """bounty.fishing (OpenCart) scraper'is.

    Tiekėjas failų nepateikia – visas katalogas surenkamas iš svetainės:
    /catall/?limit=N sąrašas + kiekvienos prekės puslapis (kodas, likutis,
    kaina, RRP, aprašymas, nuotrauka, breadcrumbs).

    config: username, password, login_url, base_url, catalog_path,
    list_limit, request_delay, timeout, retries, max_products (testams),
    brand, flavor_vocabulary, filler_words, name_replacements.
    """

    code = "bounty_fishing"

    # ------------------------------------------------------------------ fetch

    def fetch(self, uploaded_path: str | None = None) -> bytes:
        if uploaded_path:
            return super().fetch(uploaded_path)

        import requests

        timeout = int(self.config.get("timeout", 30))
        delay = float(self.config.get("request_delay", 0.2))
        retries = int(self.config.get("retries", 2))
        max_products = int(self.config.get("max_products") or 0)
        base_url = str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        login_url = str(self.config.get("login_url") or DEFAULT_LOGIN_URL)
        catalog_path = str(self.config.get("catalog_path") or DEFAULT_CATALOG_PATH)
        list_limit = int(self.config.get("list_limit") or 600)

        session = requests.Session()
        session.headers.update(
            {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Devin-BountyScraper/1.0"}
        )

        username = str(self.config.get("username") or "")
        password = str(self.config.get("password") or "")
        if not username or not password:
            raise AdapterError("BountyFishing: config turi turėti username ir password.")

        login = session.post(
            login_url,
            data={"email": username, "password": password},
            timeout=timeout,
        )
        login.raise_for_status()
        if "logout" not in login.text.lower():
            raise AdapterError("BountyFishing: prisijungimas nepavyko (nėra logout nuorodos).")

        items = self._fetch_listing(session, base_url, catalog_path, list_limit, timeout)
        if max_products:
            items = items[:max_products]

        errors: list[dict[str, str]] = []
        total = len(items)
        for index, item in enumerate(items, start=1):
            try:
                self._fetch_detail(session, item, timeout)
            except Exception as exc:  # noqa: BLE001 – mirusios nuorodos ne stabdo importo
                item["detail_error"] = str(exc)[:300]
                errors.append({"url": item["url"], "code": item.get("code", ""), "error": str(exc)[:300]})
                logger.warning("BountyFishing detail klaida %s: %s", item["url"], exc)
            if delay and index < total:
                time.sleep(delay)

        return json.dumps(
            {"items": items, "errors": errors},
            ensure_ascii=False,
        ).encode("utf-8")

    def _fetch_listing(
        self,
        session,
        base_url: str,
        catalog_path: str,
        list_limit: int,
        timeout: int,
    ) -> list[dict[str, Any]]:
        url = urljoin(base_url + "/", catalog_path.lstrip("/"))
        response = session.get(url, params={"limit": list_limit}, timeout=timeout)
        response.raise_for_status()
        tree = lxml_html.fromstring(response.content)

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for thumb in tree.xpath('//div[contains(@class,"product-thumb")]'):
            link_nodes = thumb.xpath('.//h4/a[@href]') or thumb.xpath('.//a[img][@href]')
            if not link_nodes:
                continue
            href = html.unescape(link_nodes[0].get("href", "").strip())
            if not href or href in seen:
                continue
            seen.add(href)
            name = " ".join((link_nodes[0].text_content() or "").split())
            price_node = thumb.xpath('.//p[contains(@class,"price")]')
            price = self._to_decimal(price_node[0].text_content() if price_node else "")
            pid = thumb.xpath('.//button[contains(@onclick,"cart.add")]/@onclick')
            product_id = ""
            if pid:
                match = re.search(r"cart\.add\('(\d+)'", pid[0])
                if match:
                    product_id = match.group(1)
            items.append(
                {
                    "url": href,
                    "list_name": name,
                    "list_price": str(price) if price is not None else "",
                    "product_id": product_id,
                }
            )
        if not items:
            raise AdapterError("BountyFishing: katalogo sąrašas tuščias – ar sesija aktyvi?")
        return items

    def _fetch_detail(self, session, item: dict[str, Any], timeout: int) -> None:
        response = session.get(item["url"], timeout=timeout)
        if response.status_code != 200:
            raise AdapterError(f"HTTP {response.status_code}")
        tree = lxml_html.fromstring(response.content)

        h1 = tree.xpath("//h1")
        title = " ".join((h1[0].text_content() or "").split()) if h1 else ""
        if "cannot be found" in title.lower() or "product not found" in response.text.lower():
            raise AdapterError("prekė nerasta (disabled)")

        info: dict[str, Any] = {"name": title or item["list_name"]}

        crumbs = [
            " ".join(a.text_content().split())
            for a in tree.xpath('//ul[contains(@class,"breadcrumb")]//a')
        ]
        # Pirmas – home, paskutinis – pati prekė.
        info["breadcrumbs"] = [c for c in crumbs[1:-1] if c]

        for li in tree.xpath('//ul[contains(@class,"list-unstyled")]/li'):
            text = " ".join(li.text_content().split())
            if text.startswith("Product Code:"):
                info["code"] = text.split(":", 1)[1].strip()
            elif text.startswith("Availability:"):
                info["stock_text"] = text.split(":", 1)[1].strip()

        price_node = tree.xpath('//ul[contains(@class,"list-unstyled")]//h2')
        if price_node:
            info["price"] = " ".join(price_node[0].text_content().split())

        spec: dict[str, str] = {}
        spec_table = tree.xpath('//div[@id="tab-specification"]//table//tr')
        for tr in spec_table:
            tds = [" ".join(td.text_content().split()) for td in tr.xpath("./td")]
            if len(tds) == 2:
                spec[tds[0]] = tds[1]
        if spec:
            info["spec"] = spec

        desc = tree.xpath('//div[@id="tab-description"]')
        if desc:
            node = desc[0]
            inner = [node.text or ""]
            inner += [
                lxml_html.tostring(child, encoding="unicode", method="html")
                for child in node
            ]
            info["description"] = "".join(inner).strip()

        info["images"] = [
            _original_image(html.unescape(a.get("href", "")))
            for a in tree.xpath('//a[contains(@class,"thumbnail")][@href]')
            if a.get("href")
        ]
        info["has_options"] = bool(tree.xpath('//*[@name and contains(@name,"option[")]'))
        info["detail_fetched"] = True
        item.update(info)

    # ------------------------------------------------------------------ parse

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        data = json.loads(payload.decode("utf-8"))
        if isinstance(data, str):  # dvigubai enkoduotas JSON (uploado artefaktas)
            data = json.loads(data)
        rows: list[dict[str, Any]] = list(data.get("items") or [])

        for row in rows:
            row["code"] = self._derive_code(row)
            base, axes = self._parse_name(row.get("name") or row.get("list_name") or "")
            row["base"] = base
            row["axes"] = axes

        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            leaf = (row.get("breadcrumbs") or [""])[-1].strip().lower()
            groups[(leaf, (row["base"] or "").lower())].append(row)

        for (leaf, _base), members in groups.items():
            codes = [m["code"] for m in members if m.get("code")]
            group_code = _common_prefix(codes) if len(codes) > 1 else ""
            if len(group_code) < 2:
                group_code = _slug(members[0]["base"])[:64]
            for member in members:
                member["product_group"] = group_code
                member["sub_category_leaf"] = leaf
                yield member

    def _derive_code(self, row: dict[str, Any]) -> str:
        code = (row.get("code") or "").strip()
        if code:
            return code
        url = row.get("url") or ""
        if "index.php" not in url:
            return url.rstrip("/").rsplit("/", 1)[-1].upper()
        match = re.search(r"product_id=(\d+)", url)
        return f"PID{match.group(1)}" if match else ""

    def _parse_name(self, name: str) -> tuple[str, dict[str, str]]:
        name = _collapse_duplicated(html.unescape(name).strip())
        name = _apply_replacements(name, self._name_replacements())

        axes: dict[str, str] = {}
        rest = name
        for axis, pattern in _MEAS_PATTERNS.items():
            match = pattern.search(rest)
            if match:
                axes[axis] = re.sub(r"\s+", " ", match.group(0)).strip()
                rest = pattern.sub(" ", rest)

        flavor_re = _flavor_regex(self._flavor_vocabulary())
        if flavor_re:
            match = flavor_re.search(rest)
            if match:
                axes["flavor"] = match.group(1).strip()
                rest = rest[: match.start()] + " " + rest[match.end() :]

        fillers = {w.lower() for w in self._filler_words()}
        words = [w for w in re.split(r"\s+", rest.strip()) if w.lower() not in fillers]
        base = re.sub(r"\s+", " ", " ".join(words)).strip(" -–—")
        return base or name, axes

    def _flavor_vocabulary(self) -> list[str]:
        vocab = self.config.get("flavor_vocabulary")
        return list(vocab) if vocab else list(DEFAULT_FLAVORS)

    def _filler_words(self) -> list[str]:
        words = self.config.get("filler_words")
        return list(words) if words else list(DEFAULT_FILLER_WORDS)

    def _name_replacements(self) -> dict[str, str]:
        repl = self.config.get("name_replacements")
        return dict(repl) if repl else dict(DEFAULT_NAME_REPLACEMENTS)

    # --------------------------------------------------------------- normalize

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        code = (row.get("code") or "").strip()
        if not code:
            raise AdapterError(f"BountyFishing: trūksta kodo: {repr(row)[:200]}")

        name = (row.get("name") or row.get("list_name") or "").strip()
        if not name:
            raise AdapterError(f"BountyFishing: tuščias pavadinimas ({code})")

        brand = str(self.config.get("brand") or "BOUNTY").strip()
        base = (row.get("base") or name).strip()
        base_name = base
        if brand and not base.lower().startswith(brand.lower()):
            base_name = f"{brand} {base}".strip()

        stock_text = (row.get("stock_text") or "").strip()
        stock = self._to_int(stock_text)
        out_of_stock = stock_text.lower() in {"out of stock", "prekės nėra"} or stock <= 0
        detail_missing = bool(row.get("detail_error"))
        is_available = not out_of_stock and not detail_missing

        price = self._to_decimal(row.get("price"))
        if price is None:
            price = self._to_decimal(row.get("list_price"))

        spec = row.get("spec") or {}
        rrp = self._to_decimal(spec.get("RPC"))

        images = [u for u in (row.get("images") or []) if u]
        axes = row.get("axes") or {}
        attributes: dict[str, Any] = {}
        if axes:
            attributes["axes"] = axes
        other_spec = {k: v for k, v in spec.items() if k.upper() != "RPC"}
        if other_spec:
            attributes["spec"] = other_spec

        identifiers = {
            "art_no": code,
            "product_group": row.get("product_group", code),
            "product_id": row.get("product_id") or None,
        }
        extra = {
            "identifiers": identifiers,
            "base_name": base_name,
            "product_group": row.get("product_group", code),
            "sub_category_raw": (row.get("breadcrumbs") or [""])[-1],
            "size_raw": axes.get("size"),
            "unit_weight_raw": axes.get("weight"),
            "package_raw": axes.get("pack"),
            "rrp_price": str(rrp) if rrp is not None else None,
            "main_image_url": images[0] if images else "",
            "source_url": row.get("url"),
            "stock_text": stock_text,
            "has_options": row.get("has_options"),
            "detail_error": row.get("detail_error"),
        }
        return NormalizedItem(
            external_id=code,
            sku=code,
            name=name,
            brand_name=brand,
            category_path=" > ".join(row.get("breadcrumbs") or []),
            description=(row.get("description") or "").strip(),
            attributes=attributes,
            images=images,
            stock=stock,
            purchase_price=price,
            currency="EUR",
            is_available=is_available,
            extra={k: v for k, v in extra.items() if v not in (None, "", {})},
            raw=self._clean_dict(row),
        )

    def _clean_dict(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): self._clean_dict(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._clean_dict(v) for v in value]
        if isinstance(value, Decimal):
            return float(value)
        return value
