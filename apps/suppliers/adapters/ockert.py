from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from lxml import html as lxml_html

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://shop.ockert.net"
DEFAULT_LOGIN_PATH = "/user/login"
DEFAULT_LOGIN_API = "/User/doLogin"
DEFAULT_ITEMS_API = "/remote/Inerp.cfc?method=loadPricesAvailablities"
DEFAULT_CATALOG_PATH = "/Kataloge"

# Likutis svetainėje – tik spalvos statusas; žalia ir geltona = galima
# pirkti (buy mygtukas slepiamas tik su 'red'), raudona -> 0.
DEFAULT_STOCK_BY_STATUS = {"green": 20, "yellow": 10}

# Stulpeliai, kurie nėra atributai (identifikatoriai/komercija/UI).
_LABEL_ART = re.compile(r"^art[.\-\s]?n?r?\.?$", re.IGNORECASE)
_LABEL_DESCRIPTOR = {
    "modell nr.", "bezeichnung", "kurz bezeichnung", "bez.", "modell",
}
_LABEL_SKIP = re.compile(
    r"^(pos\.?|verfügbar\.?|verfugbar\.?|verfügbarkeit|verfugbarkeit|"
    r"hek|anzahl|merkl\.?|merkliste|ve)$",
    re.I,
)
_LABEL_VE = re.compile(r"^ve$", re.IGNORECASE)
_LABEL_UVP = re.compile(r"^uvp$", re.IGNORECASE)

# Spec stulpelio label regex -> axis. Vienetas imamas iš label, jei reikšmė
# jo neturi ('Tragkraft in kg' -> '1.2kg', 'Tragkraft in lbs' -> '8lb').
_SPEC_AXIS_RULES: list[tuple[str, str]] = [
    (r"^l \(ft\)$", "rod_length_ft"),
    (r"^l \(cm\)$", "rod_length_m"),      # '300' -> '3m'
    (r"^teile$", "sections"),
    (r"durchm|ø", "line_diameter"),
    (r"tragkraft", "line_strength"),
    (r"^wg \(gr\)$", "_wg"),              # >20 g casting_weight, <=20 lb testcurve
]
_UNIT_RE = re.compile(r"\b(kg|lbs?|lb|mm|cm|m)\s*$", re.IGNORECASE)
_NONZERO_VALUE_RE = re.compile(r"[a-zA-Z]")

# Spalvų žodynas (DE+EN) – h2/descriptor uodegos -> 'color' ašis.
_COLOR_WORDS = {
    "transparent", "chartreuse", "yellow", "gelb", "black", "schwarz",
    "olive", "green", "grün", "red", "rot", "blue", "blau", "white",
    "weiss", "weiß", "camo", "brown", "braun", "orange", "pink", "grey",
    "gray", "grau", "silver", "silber", "gold", "purple", "natural",
    "hi-vis", "hivis", "smoke", "clear",
    "dunkelgrau", "hellgrau", "dunkelblau", "hellblau", "dunkelgrün",
}
_SHADE_MODIFIERS = {"dark", "light", "dunkel", "hell", "matte", "matt"}

# 'Kurz Bezeichnung' / 'Bezeichnung' tekstai -> ašys.
_SIZE_TAIL_RE = re.compile(r"\bsize\s+([A-Za-z0-9]+)\b", re.IGNORECASE)
_HOOK_RE = re.compile(r"#\s*(\d+(?:\s*/\s*\d+)?)\b")
_LB_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*lbs?\b", re.IGNORECASE)
# '3.500m' / '1,000m' – tūkstančių skyriklis (DE), ne dešimtainė dalis.
_METER_RE = re.compile(r"\b(\d{1,2}[.,]\d{3}|\d{2,4})\s*m\b", re.IGNORECASE)
_MM_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*mm\b", re.IGNORECASE)

_SLUG_ID_RE = re.compile(r"-(\d+)(?:/)?$")

# h2 fragmentai be variantinės reikšmės ('inkl. Quetschhülsen').
_H2_NOISE = {"inkl. quetschhülsen", "inkl quetschhülsen"}

_WORD_RE = re.compile(r"[^a-z0-9]+")

# Vieno puslapio lentelės (h2), kurios skiriasi ne tik spalva / ilgiu / ritės
# dydžiu, yra atskiros prekės ('Seamaster Extreme Leader' vs 'Seamaster
# Fluorocarbon Leader'); meškerių serijų lentelės lieka variantais.
_GROUP_LENGTH_RE = re.compile(r"\b\d{1,5}(?:[.,]\d+)?\s*(?:cm|m)\b", re.IGNORECASE)
_GROUP_SPOOL_RE = re.compile(r"\b\d+\s*/\s*\d+\s*lbs?\b", re.IGNORECASE)
_GROUP_VARIANT_WORDS = _COLOR_WORDS | {
    "oliv", "camou", "camouflage", "silt", "weed", "gravel", "multicolor",
}

# Outlet ('Auslauf') meškerės svetainėje neturi serijos kategorijos – tipas
# imamas iš pavadinimo (pirmas atitikmuo laimi).
DEFAULT_OUTLET_ROD_TYPES = (
    (r"meeres|seacast|neptoon", "Meeresruten"),
    (r"waller|\bcat\b", "Wallerruten"),
    (r"\bfly\b", "Fliegenruten"),
    (r"carp|stalker", "Karpfenruten"),
    (r"feeder|picker", "Feederruten"),
    (r"trowler|troll", "Trollingruten"),
)
DEFAULT_OUTLET_ROD_FALLBACK = "Spinnruten"


def _text(node) -> str:
    return " ".join((node.text_content() or "").split())


def _is_outlet(label: str) -> bool:
    low = label.lower()
    return "auslauf" in low or "sale" in low


def _outlet_rod_type(name: str) -> str:
    for pattern, rod_type in DEFAULT_OUTLET_ROD_TYPES:
        if re.search(pattern, name, re.IGNORECASE):
            return rod_type
    return DEFAULT_OUTLET_ROD_FALLBACK


def _norm_label(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip()).lower()


def _slug(text: str) -> str:
    return _WORD_RE.sub("-", text.strip().lower()).strip("-")


def _product_group_from_url(url: str) -> str:
    """'.../Produkt/Advancer-Carp-CS-3-16902437/' -> 'advancer-carp-cs-3'."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    slug = _SLUG_ID_RE.sub("", slug)
    return slug.lower().strip("-")


def _pop_color(text: str) -> tuple[str, str]:
    """Uodegos galas iš spalvų žodyno -> ('likutis', 'color')."""
    words = text.split()
    if not words or words[-1].lower() not in _COLOR_WORDS:
        return text, ""
    if len(words) > 1 and words[-2].lower() in _SHADE_MODIFIERS:
        return " ".join(words[:-2]).strip(" -–"), f"{words[-2]} {words[-1]}".title()
    return " ".join(words[:-1]).strip(" -–"), words[-1].title()


def _variant_name(product_name: str, h2: str, descriptor: str) -> str:
    """Varianto vardas: descriptor pilnas; be jo – h2 arba product+h2,
    nededuplikuojant bendrų žodžių ('Cult Catfish X-Treme' + '... Braid')."""
    if descriptor:
        # Plikas modelio kodas ('SK2912', 'FL2613') – pilnas vardas, ne vardas.
        if " " not in descriptor and len(descriptor) <= 14 and product_name:
            return f"{product_name} {descriptor}"
        return descriptor
    if not h2 or h2.lower() == product_name.lower():
        return product_name
    if product_name.lower() in h2.lower():
        return h2
    unique = " ".join(
        w for w in h2.split() if w.lower() not in product_name.lower().split()
    )
    return f"{product_name} {unique}".strip() if unique else product_name


def _descriptor_axes(descriptor: str, axes: dict[str, str]) -> None:
    """Laisvo teksto ('20lb 20cm Ronnie Rig #2') -> ašys (tik tuščias pildo)."""
    match = _SIZE_TAIL_RE.search(descriptor)
    if match:
        axes.setdefault("size", match.group(1))
    match = _LB_RE.search(descriptor)
    if match:
        axes.setdefault("line_strength", f"{match.group(1)}lb")
    match = _METER_RE.search(descriptor)
    if match:
        axes.setdefault("line_length", f"{re.sub(r'[.,]', '', match.group(1))}m")
    match = _MM_RE.search(descriptor)
    if match:
        axes.setdefault("line_diameter", f"{match.group(1)}mm")
    match = _HOOK_RE.search(descriptor)
    if match:
        axes.setdefault("hook_size", match.group(1))
    _rest, color = _pop_color(descriptor)
    if color:
        axes.setdefault("color", color)


def _h2_product_words(h2: str) -> list[str]:
    """h2 be ilgio, ritės dydžio ir spalvų – žodžiai, kurie įvardija prekę."""
    text = _GROUP_SPOOL_RE.sub(" ", _GROUP_LENGTH_RE.sub(" ", h2 or ""))
    return [
        w for w in text.split()
        if w.lower().strip('"') not in _GROUP_VARIANT_WORDS and w not in {"-", "–"}
    ]


def _split_mixed_pages(rows: list[dict[str, Any]]) -> None:
    """Skirtingų prekių lentelės viename puslapyje -> atskiri product_group."""
    pages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        pages[row.get("product_group") or ""].append(row)
    for group, page_rows in pages.items():
        crumbs = [c for c in (page_rows[0].get("category_path") or []) if c]
        catalog = (crumbs[0] if crumbs else "").lower()
        if not group or catalog.endswith("ruten"):
            continue
        keys = {
            " ".join(_h2_product_words(r.get("table_title") or "")).lower()
            for r in page_rows
        }
        if len(keys) < 2:
            continue
        for row in page_rows:
            words = _h2_product_words(row.get("table_title") or "")
            key = " ".join(words).lower()
            product_name = (row.get("product_name") or "").strip()
            if not key or key == product_name.lower():
                continue
            name = " ".join(words)
            overlap = set(key.split()) & set(product_name.lower().split())
            generic = catalog.startswith("kryston") or "bekleidung" in catalog
            if not overlap and not generic and product_name:
                name = f"{product_name} {name}"
            row["page_product_name"] = product_name
            row["product_name"] = name
            row["product_group"] = f"{group}-{_slug(key)}"


@register_adapter
class OckertAdapter(BaseAdapter):
    """shop.ockert.net (ColdFusion/INERP B2B) scraper'is.

    Tiekėjas feed'o neturi; svetainė už login'o, katalogas – medis
    /Kataloge -> /Katalog -> /Kategorie -> /Produkt, variantai –
    HTML lentelės per /Inerp:Product/showProductItems, kainos/likučiai –
    POST /remote/Inerp.cfc?method=loadPricesAvailablities.

    Login'as: POST /User/doLogin {username, password: MD5(password)}.

    config: username, password, base_url, catalog_path, request_delay,
    timeout, retries, max_products (testams), stock_by_status
    (default {"green": 20, "yellow": 10}),
    brand_map (katalogo pavadinimas -> brand), default_brand.
    """

    code = "ockert"

    # ------------------------------------------------------------------ fetch

    def fetch(self, uploaded_path: str | None = None) -> bytes:
        if uploaded_path or self.feed.file:
            return super().fetch(uploaded_path)

        import requests

        timeout = int(self.config.get("timeout", 30))
        delay = float(self.config.get("request_delay", 0.2))
        retries = int(self.config.get("retries", 2))
        max_products = int(self.config.get("max_products") or 0)
        base_url = str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        catalog_path = str(self.config.get("catalog_path") or DEFAULT_CATALOG_PATH)
        username = str(self.config.get("username") or "")
        password = str(self.config.get("password") or "")
        if not username or not password:
            raise AdapterError("Ockert: config turi turėti username ir password.")

        session = requests.Session()
        session.headers.update(
            {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Devin-OckertScraper/1.0"}
        )
        self._login(session, base_url, username, password, timeout)

        products = self._walk_catalog(
            session, base_url, catalog_path, timeout, delay, retries
        )
        if max_products:
            products = products[:max_products]
        if not products:
            raise AdapterError("Ockert: kataloge nerasta nė vieno produkto.")

        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        total = len(products)
        for index, product in enumerate(products, start=1):
            try:
                items.extend(
                    self._fetch_product(session, base_url, product, timeout, retries)
                )
            except Exception as exc:  # noqa: BLE001 – viena prekė nestabdo importo
                product_err = {"url": product["url"], "error": str(exc)[:300]}
                errors.append(product_err)
                logger.warning("Ockert produkto klaida %s: %s", product["url"], exc)
            if delay and index < total:
                time.sleep(delay)

        return json.dumps(
            {"items": items, "errors": errors},
            ensure_ascii=False,
        ).encode("utf-8")

    def _login(self, session, base_url: str, username: str, password: str, timeout: int) -> None:
        login_page = f"{base_url}{self.config.get('login_path') or DEFAULT_LOGIN_PATH}"
        api = f"{base_url}{self.config.get('login_api') or DEFAULT_LOGIN_API}"
        page = session.get(login_page, timeout=timeout)
        page.raise_for_status()
        response = session.post(
            api,
            data={
                "username": username,
                "password": hashlib.md5(password.encode()).hexdigest(),
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=timeout,
        )
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError as exc:
            raise AdapterError(
                f"Ockert: login API atsakė ne JSON ({response.status_code})"
            ) from exc
        if not result.get("success"):
            raise AdapterError(
                f"Ockert: prisijungimas nepavyko: {result.get('message') or result}"
            )

    def _get(self, session, url: str, timeout: int, retries: int):
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                response = session.get(url, timeout=timeout)
                if response.status_code == 200:
                    return response
                last_exc = AdapterError(f"HTTP {response.status_code}")
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
        raise last_exc or AdapterError("užklausa nepavyko")

    def _walk_catalog(
        self, session, base_url: str, catalog_path: str, timeout: int, delay: float, retries: int
    ) -> list[dict[str, Any]]:
        """BFS per /Katalog + /Kategorie medį -> [{url, path, name}] produktams."""
        seen_pages: set[str] = set()
        # Tas pats produktas pasiekiamas per kelis kelius (pvz. outlet
        # 'Auslauf Ruten' rodo likučio prekes iš visų serijų) – renkame
        # visas kandidatų kelias ir paskui imame giliausią ne-outlet.
        paths: dict[str, list[list[str]]] = {}
        # (url, vardų kelias iki šio puslapio)
        queue: list[tuple[str, list[str]]] = [(catalog_path, [])]
        while queue:
            path, crumbs = queue.pop(0)
            if path in seen_pages:
                continue
            seen_pages.add(path)
            response = self._get(session, base_url + path, timeout, retries)
            tree = lxml_html.fromstring(
                response.content.decode("utf-8", errors="replace")
            )

            for anchor in tree.xpath("//a[@href]"):
                href = (anchor.get("href") or "").split("?")[0].split("#")[0]
                if not href.startswith("/"):
                    continue
                label = _text(anchor)
                if href.startswith("/Produkt/"):
                    paths.setdefault(href, [])
                    if crumbs and crumbs not in paths[href]:
                        paths[href].append(list(crumbs))
                elif href.startswith("/Katalog/") or href.startswith("/Kategorie/"):
                    if href not in seen_pages and label:
                        queue.append((href, [*crumbs, label]))
            if delay:
                time.sleep(delay)
        return [
            {"url": url, "category_path": self._best_path(candidates)}
            for url, candidates in paths.items()
        ]

    @staticmethod
    def _best_path(candidates: list[list[str]]) -> list[str]:
        """Iš kelių kategorijų kelių – giliausias, 'Auslauf'/'Sale'
        outlet kategorijas atstumiant (ten rodomas svetimas inventorius)."""

        def score(crumbs: list[str]) -> tuple[int, int]:
            outlet = any(_is_outlet(c) for c in crumbs)
            return (0 if outlet else 1, len(crumbs))

        return max(candidates, key=score) if candidates else []

    def _fetch_product(
        self, session, base_url: str, product: dict[str, Any], timeout: int, retries: int
    ) -> list[dict[str, Any]]:
        response = self._get(session, base_url + product["url"], timeout, retries)
        tree = lxml_html.fromstring(
            response.content.decode("utf-8", errors="replace")
        )

        h1 = tree.xpath("//h1")
        name = _text(h1[0]) if h1 else ""
        image = ""
        img = tree.xpath('//img[contains(@src,"/cust/images/")]/@src')
        if img:
            image = img[0]
        og = tree.xpath('//meta[@property="og:image"]/@content')
        if not image and og:
            image = og[0]
        if image and image.startswith("/"):
            image = base_url + image

        items_url = ""
        match = re.search(
            r"/Inerp:Product/showProductItems\?[^\"'\s>]+", response.text
        )
        if match:
            items_url = match.group(0)
        if not items_url:
            raise AdapterError("nėra showProductItems")

        items_resp = self._get(session, base_url + items_url, timeout, retries)
        rows = self._parse_items_html(items_resp.content)

        self._fill_availability(session, base_url, rows, timeout)

        out: list[dict[str, Any]] = []
        for row in rows:
            row.update(
                {
                    "product_url": product["url"],
                    "product_name": name,
                    "category_path": product["category_path"],
                    "image": image,
                }
            )
            out.append(row)
        return out

    def _parse_items_html(self, content: bytes) -> list[dict[str, Any]]:
        """showProductItems HTML -> eilučių įrašai (po vieną Art.Nr.)."""
        tree = lxml_html.fromstring(content.decode("utf-8", errors="replace"))
        rows: list[dict[str, Any]] = []
        current_h2 = ""
        container = tree.xpath('//div[contains(@class,"js-productdetail-articlelists")]')
        if not container:
            return rows
        for node in container[0]:
            tag = node.tag if isinstance(node.tag, str) else ""
            if tag == "h2":
                current_h2 = _text(node)
                continue
            for table in node.xpath(".//table"):
                rows.extend(self._parse_table(table, current_h2))
        return rows

    def _parse_table(self, table, h2: str) -> list[dict[str, Any]]:
        headers = [_norm_label(_text(th)) for th in table.xpath(".//thead//th")]
        out: list[dict[str, Any]] = []
        for tr in table.xpath('.//tbody//tr[contains(@class,"js-itemRow")]') or table.xpath(".//tbody//tr[td]"):
            cells = tr.xpath("./td")
            record: dict[str, Any] = {"table_title": h2}
            specs: dict[str, str] = {}
            for idx, td in enumerate(cells):
                label = headers[idx] if idx < len(headers) else ""
                value = " ".join((td.text_content() or "").split())
                if _LABEL_ART.match(label):
                    record["art_no"] = value
                elif label in _LABEL_DESCRIPTOR:
                    record["descriptor"] = value
                elif _LABEL_VE.match(label):
                    record["ve"] = value
                elif _LABEL_UVP.match(label):
                    record["uvp"] = value
                elif _LABEL_SKIP.match(label):
                    continue
                elif label and value:
                    specs[label] = value
            record["specs"] = specs

            item_id = tr.get("data-ws-itemId") or tr.get("data-ws-itemid") or ""
            record["item_id"] = item_id
            availability_type = tr.get("data-ws-availabilityType") or ""
            record["availability_type"] = availability_type

            price_span = tr.xpath('.//span[contains(@class,"itemPrice")]')
            if price_span:
                record["hek"] = " ".join(price_span[0].text_content().split())
            min_order = tr.xpath(".//@data-ws-minOrderQuantity")
            if min_order:
                record["min_order_qty"] = min_order[0]
            itemoid = tr.xpath(".//@data-ws-itemoid")
            if itemoid:
                record["item_oid"] = itemoid[0]
            avail_span = tr.xpath('.//span[contains(@class,"js-item-availability")]/@class')
            if avail_span:
                match = re.search(r"js-item-availability-_(\S+)", avail_span[0])
                if match:
                    record["availability_key"] = "_" + match.group(1)
            if record.get("art_no") or record.get("item_id"):
                out.append(record)
        return out

    def _fill_availability(
        self, session, base_url: str, rows: list[dict[str, Any]], timeout: int
    ) -> None:
        """POST loadPricesAvailablities -> row['availability'], row['stock_text']."""
        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row.get("item_id"):
                by_type[row.get("availability_type") or ""].append(row)
        api = base_url + (self.config.get("items_api") or DEFAULT_ITEMS_API)
        for availability_type, group in by_type.items():
            try:
                response = session.post(
                    api,
                    data={
                        "itemIds": ",".join(r["item_id"] for r in group),
                        "isGetItempricelistpricequantities": "false",
                        "isGetAvailabilities": "true",
                        "availabilityType": availability_type or "1211",
                        "isUseNetPrices": "true",
                        "isCheckOrderpositions": "false",
                        "watchlistId": "-1",
                        "documenthistoryId": "-1",
                    },
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=timeout,
                )
                response.raise_for_status()
                data = json.loads(response.text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ockert availability API klaida: %s", exc)
                continue
            for element in data:
                key = str(element.get("itemOidCFVar") or "")
                availability = str(element.get("availability") or "").lower()
                content = str(element.get("elementContent") or "")
                label_match = re.search(r'data-content="([^"]+)"', content)
                stock_text = label_match.group(1) if label_match else availability
                for row in group:
                    if (
                        row.get("availability_key") == key
                        or row.get("item_oid") == key.lstrip("_")
                        or row.get("art_no") == key.lstrip("_")
                    ):
                        row["availability"] = availability
                        row["stock_text"] = stock_text

    # ------------------------------------------------------------------ parse

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        data = json.loads(payload.decode("utf-8"))
        if isinstance(data, str):
            data = json.loads(data)
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for row in data.get("items") or []:
            art_no = (row.get("art_no") or "").strip()
            if not art_no:
                continue
            if art_no in seen:
                row["duplicate_art_no"] = True
            seen.add(art_no)
            row["product_group"] = _product_group_from_url(row.get("product_url") or "")
            rows.append(row)
        _split_mixed_pages(rows)
        yield from rows

    # --------------------------------------------------------------- normalize

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        art_no = (row.get("art_no") or "").strip()
        if not art_no:
            raise AdapterError(f"Ockert: trūksta Art.Nr.: {repr(row)[:200]}")

        product_name = (row.get("product_name") or "").strip()
        h2 = (row.get("table_title") or "").strip()
        descriptor = (row.get("descriptor") or "").strip()

        name = _variant_name(product_name, h2, descriptor) or art_no

        crumbs = [c for c in (row.get("category_path") or []) if c]
        if crumbs and crumbs[0].lower().endswith("ruten") and _is_outlet(crumbs[-1]):
            crumbs.append(_outlet_rod_type(f"{product_name} {name}"))
        category_path = " > ".join(crumbs)
        catalog = crumbs[0] if crumbs else ""
        sub_category = crumbs[-1] if crumbs else ""
        family = crumbs[-2] if len(crumbs) > 1 else ""

        brand = self._brand_for(catalog, name)

        axes, specs = self._row_axes_specs(row, h2, product_name)
        descriptor_axes_input = descriptor or h2
        if descriptor_axes_input:
            _descriptor_axes(descriptor_axes_input, axes)

        attributes: dict[str, Any] = {}
        if axes:
            attributes["axes"] = axes
        if specs:
            attributes["specs"] = specs

        stock_map = self.config.get("stock_by_status") or DEFAULT_STOCK_BY_STATUS
        availability = (row.get("availability") or "").strip().lower()
        stock = int(stock_map.get(availability, 0) or 0)
        is_available = stock > 0

        price = self._to_decimal(row.get("hek"))
        rrp = self._to_decimal(row.get("uvp"))

        base_name = product_name or name
        if brand and not base_name.lower().startswith(brand.lower()):
            base_name = f"{brand} {base_name}".strip()

        image = (row.get("image") or "").strip()
        images = [image] if image else []

        # VE = Verpackungseinheit: tiekėjui užsakome pakais, bet parduodame
        # po 1 vnt. (HEK/UVP – vieneto kainos). VE lieka trade_pack_qty
        # (B2B ateičiai), sold_as_pack nenustatomas.
        pack_qty = self._to_int(row.get("ve") or row.get("min_order_qty"))
        extra = {
            "identifiers": {"art_no": art_no, "item_id": row.get("item_id")},
            "base_name": base_name,
            "product_group": row.get("product_group", ""),
            "sub_category_raw": sub_category,
            "family_raw": family,
            "rrp_price": str(rrp) if rrp is not None else None,
            "main_image_url": image,
            "source_url": row.get("product_url"),
            "stock_text": row.get("stock_text") or row.get("availability"),
            "trade_pack_qty": pack_qty if pack_qty > 1 else None,
            "min_order_qty": row.get("min_order_qty"),
            "duplicate_art_no": row.get("duplicate_art_no"),
        }
        return NormalizedItem(
            external_id=art_no,
            sku=art_no,
            name=name,
            brand_name=brand,
            category_path=category_path,
            attributes=attributes,
            images=images,
            stock=stock,
            purchase_price=price,
            currency="EUR",
            is_available=is_available,
            extra={k: v for k, v in extra.items() if v not in (None, "", {})},
            raw=self._clean(row),
        )

    def _brand_for(self, catalog: str, name: str) -> str:
        # Vardo prefiksas – pirmiausia ('Krystonite' Angelschnüre viduje
        # -> Kryston, nors katalogas yra Climax).
        prefixes = self.config.get("brand_by_name_prefix") or {}
        first = (name.split() or [""])[0]
        for prefix, brand in prefixes.items():
            if first.lower() == prefix.lower():
                return str(brand)
        brand_map = self.config.get("brand_map") or {}
        for key, brand in brand_map.items():
            if key.lower() in (catalog or "").lower():
                return str(brand)
        default = str(self.config.get("default_brand") or "")
        if default:
            return default
        return first if first and first.lower() not in {"die", "der", "das"} else ""

    def _row_axes_specs(
        self, row: dict[str, Any], h2: str, product_name: str
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Spec stulpeliai + h2 -> (axes, specs)."""
        axes: dict[str, str] = {}
        specs: dict[str, str] = dict(row.get("specs") or {})
        for label, value in list(specs.items()):
            norm = _norm_label(label)
            axis = ""
            for pattern, target in _SPEC_AXIS_RULES:
                if re.search(pattern, norm):
                    axis = target
                    break
            if not axis:
                continue
            del specs[label]
            number = self._to_decimal(value)
            if number is not None and number == 0:
                continue  # '0'/'0.0' ląstelės = be reikšmės
            if axis == "rod_length_m":
                if number is not None:
                    axes[axis] = f"{format(number / 100, 'f').rstrip('0').rstrip('.')}m"
            elif axis == "_wg":
                if number is not None:
                    # 'WG (gr)' label klaidingas: >12 – gramai (casting
                    # weight), <=12 – lb testcurve / AFTMA klasė.
                    if number > 12:
                        axes["casting_weight"] = f"{format(number, 'f')}g"
                    else:
                        axes["testcurve"] = f"{format(number, 'f')}lb"
            elif value:
                if number is not None and not _NONZERO_VALUE_RE.search(value):
                    unit = _UNIT_RE.search(norm)
                    if unit:
                        value = f"{format(number, 'f')}{unit.group(1).lower()}"
                axes[axis] = value

        # h2 = variantų grupės pavadinimas ('Hoodie yellow', 'transparent 1.000m')
        if h2 and h2.lower() != (product_name or "").lower():
            # Atimam produkto vardo žodžius: 'Cult Catfish X-Treme Braid' -
            # 'Cult Catfish X-Treme' -> 'Braid'; sutampantis h2 -> be model.
            product_words = set((product_name or "").lower().split())
            rest = " ".join(
                w for w in h2.split() if w.lower() not in product_words
            )
            rest, color = _pop_color(rest)
            if color:
                axes.setdefault("color", color)
            match = re.search(r"\b(\d{1,5})(?:[.,](\d+))?\s*(cm|m)\b", rest)
            if match:
                value = f"{match.group(1)}{'.' + match.group(2) if match.group(2) else ''}"
                if match.group(3) == "m" and match.group(2) and len(match.group(2)) == 3:
                    value = f"{match.group(1)}{match.group(2)}"  # '3.500m' = 3500 m
                if match.group(3) == "cm":
                    number = self._to_decimal(value)
                    if number is not None:
                        value = f"{format(number / 100, 'f').rstrip('0').rstrip('.')}"
                axes.setdefault("line_length", f"{value}m")
                rest = (rest[: match.start()] + rest[match.end() :]).strip(" -–")
            rest = re.sub(r"\s+", " ", rest).strip(" -–")
            if rest and rest.lower() not in _H2_NOISE:
                axes.setdefault("model", rest)

        # Ritės: dydis slypi Art.Nr./descriptor pabaigoje ('FN1500' -> '1500').
        if not axes:
            context = " ".join(row.get("category_path") or []).lower()
            art_no = (row.get("art_no") or "").strip()
            if "angelrollen" in context or "rollen" in context:
                match = re.search(r"(\d+[A-Za-z]*)$", art_no.upper())
                if match:
                    axes["reel_size"] = match.group(1)
        return axes, specs

    @staticmethod
    def _clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): OckertAdapter._clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [OckertAdapter._clean(v) for v in value]
        if isinstance(value, Decimal):
            return float(value)
        return value
