"""Albatros (AL.) – vietinis tiekėjas, „Centrinis sandėlis“.

Šaltinis – pilnas Stock Excel (`Stock 10.21.xlsx`): skyriaus eilutės (užpildytas tik A)
ir prekių eilutės `A žymė | Code | Item | be PVM, export | SALE | Q-ty | Order | FOTO`.
Planas ir sprendimai: `albatros_import.md`.

Kainodara (vartotojo sprendimas):
    cost     = export × (1 + SALE)      -> purchase_price (BE cost_eur)
    price    = export × 1,5             -> rrp_price (be PVM; pricing engine ima RRP bazę)
    discount = round(−SALE × 100)       -> discount_percent (BE offer_discount_percent)

nemus.lt (Albatros el. parduotuvė) – visas katalogas (`scrape_manufacturer --source nemus`):
    kodas ir Stock faile -> savikaina iš Albatros; kaina = min(export × 1,5, nemus be PVM),
                            galutinė = min(export × (1 + SALE) × 1,5, nemus be PVM)
    kodo Stock faile nėra -> kaina = nemus be PVM, savikaina = × 0,8, likutis 19 jei „yra“
    variantai: pavadinimo dalis po paskutinio kablelio ('..., L') -> ašis `size`
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from collections import Counter
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from openpyxl import load_workbook

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter

logger = logging.getLogger(__name__)

# Mažmenininkų puslapiai kodams be gamintojo turinio, pirmumo tvarka.
RETAIL_SOURCES = ("nemus", "zuklys")
PRICE_FACTOR = Decimal("1.5")
CENT = Decimal("0.01")
NEMUS = "nemus"
VAT = Decimal("1.21")
NEMUS_COST_FACTOR = Decimal("0.8")
# nemus „yra“ = prekė parduotuvėje (pristatymas kaip iš Centrinio sandėlio)
NEMUS_IN_STOCK = 19
_MAX_VARIANT_LEN = 40

# Antraštės (lower), pagal kurias atpažįstamas pilnas Stock failas
_REQUIRED = {"code": "code", "item": "item", "export": "be pvm, export", "sale": "sale", "qty": "q-ty"}

_NEW_MARK_RE = re.compile(r"^\s*new\s*(\d{4})\s*$", re.IGNORECASE)

# Lietuviški bendriniai pirmi žodžiai – ne ženklas
_GENERIC_WORDS = {
    "VOBLERIS", "BALANSYRAS", "SUKRIUKĖ", "VARIKLIS", "BLIZGĖ", "GUMINUKAS", "JUNGTIS",
    "ŽEMĖLAPIAI", "PROPELERIS", "TVIRTINIMO", "LENTA", "ECHOLOTAS", "KREPŠYS", "TIP", "PART",
}

# (žetonai, ženklas). Tikrinama pirmuose _BRAND_SCAN žetonuose; ilgesni pirmi.
BRAND_ALIASES: list[tuple[str, str]] = [
    ("SAVAGE GEAR", "Savage Gear"), ("SAVAGEAR", "Savage Gear"), ("SAVAGE", "Savage Gear"),
    ("SG", "Savage Gear"), ("CRAZY FISH", "Crazy Fish"), ("LUCKY JOHN", "Lucky John"),
    ("LUCKY CRAFT", "Lucky Craft"), ("MINN KOTA", "Minn Kota"), ("MINNKOTA", "Minn Kota"), ("OLD TOWN", "Old Town"),
    ("ROY POW", "RoyPow"), ("ROYPOW", "RoyPow"), ("CC MOORE", "CC Moore"),
    ("BOSS AUDIO", "BOSS Audio"), ("RAM MOUNTS", "RAM Mounts"), ("RAM", "RAM Mounts"),
    ("PROLOGIC", "Prologic"), ("PL", "Prologic"), ("MADCAT", "Madcat"), ("MAD", "MAD"),
    ("DAM", "DAM"), ("MEPPS", "Mepps"), ("RAPALA", "Rapala"), ("STORM", "Storm"),
    ("KUUSAMO", "Kuusamo"), ("JOBE", "Jobe"), ("FOX", "Fox"), ("STROFT", "Stroft"),
    ("BORIKA", "Borika"), ("SPIDERWIRE", "SpiderWire"), ("SCIERRA", "Scierra"),
    ("M.A.T", "M.A.T"), ("TICA", "TiCA"), ("PENN", "Penn"), ("GREYS", "Greys"),
    ("SHIMANO", "Shimano"), ("PLANO", "Plano"), ("SPINWAL", "Spinwal"),
    ("HUMMINBIRD", "Humminbird"), ("R.T.", "Ron Thompson"), ("RT", "Ron Thompson"),
    ("NEXO", "Nexo"), ("MARINAC", "Marinac"), ("FZ", "Effzett"), ("EFFZETT", "Effzett"),
    ("REBELCELL", "RebelCell"), ("CANNON", "Cannon"), ("HERTZ", "Hertz"), ("IMAX", "Imax"),
    ("MATRIX", "Matrix"), ("TENRYU", "Tenryu"), ("SPOMB", "Spomb"), ("OKUMA", "Okuma"),
    ("PRESTON", "Preston"), ("NAVIONICS", "Navionics"), ("NAVIONICS+", "Navionics"),
    ("DEEPER", "Deeper"), ("RON THOMPSON", "Ron Thompson"), ("WATERWOLF", "WaterWolf"),
    ("ZEBCO", "Zebco"), ("JRC", "JRC"), ("EIGER", "Eiger"), ("FISHMASTER", "Fishmaster"),
    ("HANSEN", "Hansen"), ("STORFT", "Stroft"), ("PROLOGICC2", "Prologic"),
]
_BRAND_SCAN = 4


def normalize_code(value: Any) -> str:
    """143792 / 143792.0 / ' 10005-100 ' -> '143792' / '10005-100'."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def detect_brand(name: str, section: str = "", aliases=None) -> str:
    aliases = aliases or BRAND_ALIASES

    def scan(text: str) -> str:
        tokens = [t for t in re.split(r"\s+", (text or "").upper()) if t]
        tokens = [t for t in tokens[: _BRAND_SCAN + 2] if t not in _GENERIC_WORDS][:_BRAND_SCAN]
        for i in range(len(tokens)):
            for alias, brand in aliases:
                parts = alias.split()
                if tokens[i : i + len(parts)] == parts:
                    return brand
        return ""

    return scan(name) or scan(re.split(r"\s+-\s+", section or "")[0].replace("/", " "))


def badges_from_mark(mark: Any) -> list[str]:
    match = _NEW_MARK_RE.match(str(mark or ""))
    return [f"new-{match.group(1)}"] if match else []


@register_adapter
class AlbatrosAdapter(BaseAdapter):
    code = "albatros"

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        col: dict[str, int] = {}
        for row in rows:
            header = [str(c or "").strip().lower() for c in row]
            if "code" in header:
                col = {k: header.index(h) for k, h in _REQUIRED.items() if h in header}
                break
        missing = sorted(set(_REQUIRED) - set(col))
        if missing:
            # neatpažintas failas -> klaida, likučiai neliečiami
            raise AdapterError(f"Albatros: ne Stock failas, trūksta stulpelių {missing}")

        def cell(row: tuple, key: str) -> Any:
            i = col[key]
            return row[i] if i < len(row) else None

        section = ""
        items: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = normalize_code(cell(row, "code"))
            item = str(cell(row, "item") or "").strip()
            if not code and not item:
                if row and row[0] and str(row[0]).strip():
                    section = str(row[0]).strip()
                continue
            if not code:
                # prekė be kodo -> normalize klaida (importas su klaidomis likučių nenulina)
                yield {"code": "", "name": item, "section": section}
                continue
            photo = next(
                (str(c).strip() for c in row if isinstance(c, str) and c.strip().startswith("http")),
                "",
            )
            if code in items:
                logger.warning("Albatros: kodas %s kartojasi – imama paskutinė eilutė", code)
            items[code] = {
                "code": code,
                "name": re.sub(r"\s+", " ", item),
                "section": section,
                "mark": str(row[0] or "").strip() if row else "",
                "export": cell(row, "export"),
                "sale": cell(row, "sale"),
                "qty": cell(row, "qty"),
                "photo": photo,
            }
        yield from items.values()

    def iter_items(self, payload: bytes) -> Iterator[NormalizedItem]:
        # Bloga eilutė neturi nutraukti viso importo: praleidžiama ir skaičiuojama
        # fetch_errors (pipeline -> stats.errors -> mark_absent_offers likučių nenulina).
        self.fetch_errors = 0
        items = []
        failed = set()
        for row in self.parse(payload):
            try:
                items.append(self.normalize(row))
            except AdapterError as exc:
                self.fetch_errors += 1
                failed.add(str(row.get("code") or "").strip().upper())
                logger.warning("%s", exc)
        use_nemus = self.config.get("nemus_catalog", self.config.get("manufacturer_groups", True))
        nemus = nemus_contents() if use_nemus else {}
        if nemus:
            apply_nemus_prices(items, nemus)
            items.extend(nemus_items(items, nemus, self.config.get("brand_aliases"), skip=failed))
        if self.config.get("manufacturer_groups", True):
            apply_manufacturer_content(items, manufacturer_lookup(items))
        if nemus:
            apply_nemus_groups(items, nemus)
        if self.config.get("photo_groups", True):
            apply_photo_groups(items)
        apply_fixed_groups(items, self.config.get("fixed_groups") or {})
        _distinguish_twins(items)
        yield from items

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        code = row.get("code") or ""
        if not code:
            raise AdapterError(f"Albatros: trūksta kodo: {repr(row)[:200]}")
        name = row.get("name") or code
        export = self._to_decimal(row.get("export"))
        if export is not None and export < 0:
            raise AdapterError(f"Albatros {code}: neigiama export kaina ({export})")
        sale = self._to_decimal(row.get("sale"))
        if sale is not None and not Decimal(-1) < sale <= 0:
            raise AdapterError(f"Albatros {code}: SALE už ribų ({sale})")
        sale = sale or None
        cost = rrp = None
        if export is not None:
            cost = (export * (1 + (sale or 0))).quantize(CENT, rounding=ROUND_HALF_UP)
            rrp = (export * PRICE_FACTOR).quantize(CENT, rounding=ROUND_HALF_UP)
        discount = int((-sale * 100).to_integral_value(rounding=ROUND_HALF_UP)) if sale else None
        qty = self._to_decimal(row.get("qty"))
        if qty is None:
            # tuščias/netaisyklingas kiekis – eilutės klaida, ne 0 (klaida blokuoja nulinimą)
            raise AdapterError(f"Albatros {code}: netaisyklingas Q-ty ({row.get('qty')!r})")
        stock = max(int(qty), 0)
        photo = row.get("photo") or ""
        section = row.get("section") or ""
        extra = {
            "base_name": name,
            "category_raw": section,
            "rrp_price": str(rrp) if rrp is not None else None,
            "export_price": str(export) if export is not None else None,
            "sale": str(sale) if sale is not None else None,
            # ta pati nuotrauka keliems kodams – variantų grupavimo užuomina
            "photo_group": photo,
            "main_image_url": photo,
        }
        return NormalizedItem(
            external_id=code,
            sku=code,
            name=name,
            brand_name=detect_brand(name, section, self.config.get("brand_aliases")),
            category_path=section,
            images=[photo] if photo else [],
            stock=stock,
            purchase_price=cost,
            discount_percent=discount,
            badges=badges_from_mark(row.get("mark")),
            currency="EUR",
            is_available=stock > 0,
            extra={k: v for k, v in extra.items() if v not in (None, "")},
            raw={k: str(v) if v is not None else None for k, v in row.items()},
        )


def nemus_contents() -> dict[str, Any]:
    """Kodas -> ManufacturerContent(source=nemus) (vienas puslapis = vienas kodas)."""
    from apps.suppliers.models import ManufacturerContent

    out: dict[str, Any] = {}
    for content in ManufacturerContent.objects.filter(source=NEMUS).exclude(codes=[]).order_by("url"):
        for code in content.codes:
            out.setdefault(str(code).strip().upper(), content)
    return out


def _nemus_offer(content) -> dict:
    offer = (content.specs or {}).get("offer")
    return offer if isinstance(offer, dict) else {}


def nemus_net_price(content) -> Decimal | None:
    """nemus kaina su PVM -> be PVM (vidiniai skaičiavimai visada be PVM)."""
    try:
        gross = Decimal(str(_nemus_offer(content).get("price") or ""))
    except ArithmeticError:
        return None
    if not gross.is_finite() or gross <= 0:
        return None
    return (gross / VAT).quantize(CENT, rounding=ROUND_HALF_UP)


def apply_nemus_prices(items: list[NormalizedItem], nemus: dict) -> None:
    """Kodas ir Stock faile, ir nemus: savikaina iš Albatros, kaina – mažesnioji, skyrius iš nemus."""
    for item in items:
        content = nemus.get(item.external_id.upper())
        category = (content.specs or {}).get("category") if content is not None else ""
        if category:
            # nemus skyrius (LT, giliau) tikslesnis už Stock skyrių; photo grupėms lieka `category_raw`
            item.category_path = category
        net = nemus_net_price(content) if content is not None else None
        if net is None:
            continue
        item.extra["nemus_price"] = str(net)
        rrp = item.extra.get("rrp_price")
        if rrp is None or Decimal(rrp) <= 0:
            # Albatros kainos nenurodė -> kaip nemus-only prekė
            item.extra["rrp_price"] = str(net)
            item.purchase_price = (net * NEMUS_COST_FACTOR).quantize(CENT, rounding=ROUND_HALF_UP)
            item.discount_percent = None
            continue
        rrp = Decimal(rrp)
        final = rrp * (100 - (item.discount_percent or 0)) / 100
        new_rrp, new_final = min(rrp, net), min(final, net)
        item.extra["rrp_price"] = str(new_rrp)
        discount = int(((1 - new_final / new_rrp) * 100).to_integral_value(rounding=ROUND_HALF_UP))
        item.discount_percent = discount or None


def nemus_items(items: list[NormalizedItem], nemus: dict, aliases=None, skip=()) -> list[NormalizedItem]:
    """nemus prekės, kurių nėra Stock faile: kaina nemus be PVM, savikaina × 0,8.

    `skip` – klaidingų Stock eilučių kodai: jų likutis nekeičiamas nemus duomenimis."""
    known = {item.external_id.upper() for item in items} | set(skip)
    out = []
    for code, content in nemus.items():
        if code in known:
            continue
        net = nemus_net_price(content)
        if net is None:
            continue
        offer = _nemus_offer(content)
        name = content.series or code
        section = (content.specs or {}).get("category") or "Nemus"
        stock = NEMUS_IN_STOCK if offer.get("in_stock") else 0
        images = list(content.images or [])
        extra = {
            "base_name": name,
            "category_raw": section,
            "rrp_price": str(net),
            "nemus_price": str(net),
            "retail_url": content.url,
            "main_image_url": images[0] if images else "",
        }
        out.append(
            NormalizedItem(
                external_id=code,
                sku=code,
                name=name,
                brand_name=detect_brand(re.sub(r"[®™]", " ", name), "", aliases)
                or detect_brand(offer.get("brand") or "", "", aliases)
                or offer.get("brand")
                or "",
                category_path=section,
                images=images,
                stock=stock,
                purchase_price=(net * NEMUS_COST_FACTOR).quantize(CENT, rounding=ROUND_HALF_UP),
                currency="EUR",
                is_available=stock > 0,
                extra={k: v for k, v in extra.items() if v not in (None, "")},
                raw={"source": NEMUS, "url": content.url, **offer},
            )
        )
    return out


_VARIANT_COMMA = re.compile(r",(?!\d)|(?<!\d),")


def split_variant_name(name: str) -> tuple[str, str]:
    """'BRABUS x JOBE „Shadow Fragment“ plūdrumo liemenė moterims, L' -> (bazė, 'L').

    Kablelis tarp skaitmenų (dydis 9,5) – ne skirtukas."""
    name = (name or "").strip()
    cuts = [m.start() for m in _VARIANT_COMMA.finditer(name)]
    if not cuts:
        return name, ""
    base, value = name[: cuts[-1]].strip(), name[cuts[-1] + 1 :].strip()
    if not base or not value or len(value) > _MAX_VARIANT_LEN:
        return name, ""
    return base, value


_COLOR = re.compile(
    r"^(juod|pilk|mėlyn|balt|raudon|žal|geltn|gelton|oranž|rož|violet|rud|smaragd|sidabr|auks|chaki|"
    r"bord|turkio|mėtin|levand|koral|smėlin|grafit|kamufliaž|tams|šviesi|spalv|žydr|alyv|persik|"
    r"krem|bež|salotin|vyšn|fuksij|olyv|kaki|neon|black|gr[ae]y|navy|"
    r"blue|red|green|yellow|orange|pink|white|purple|camo)"
)
_COLOR_GLUE = {"/", "-", "ir", "&"}
_SIZES = {
    "XS", "S", "M", "L", "XL", "XXL", "XXXL", "2XS", "2XL", "3XL", "4XL", "5XL", "UNI", "UNIVERSAL",
    "SMALL", "MEDIUM", "LARGE", "MAŽAS", "VIDUTINIS", "DIDELIS", "DYDIS", "ONE", "SIZE", "OS",
    "UNIVERSALUS",
}


def is_size(value: str) -> bool:
    """'XL', '2XL/3XL', 'dydis 9,5 - 43', '116cm' -> True; 'žydra', 'pripučiama' -> False."""
    return any(ch.isdigit() for ch in value) or any(
        t.upper() in _SIZES for t in re.split(r"[\s/+-]+", value)
    )


def split_color(value: str) -> tuple[str, str]:
    """'smaragdinė spalva S' -> ('smaragdinė spalva', 'S'); 'juodas / pilkas' -> ('juodas / pilkas', '').

    Spalva = spalvų žodžiai ir viskas iki „spalva“ ('žalios citrinos spalva 116cm')."""
    tokens = value.split()
    words = [t.casefold().strip(".,;()") for t in tokens]
    color = [bool(_COLOR.match(w)) for w in words]
    for i, word in enumerate(words):
        if word.startswith("spalv"):
            color[i] = True
            j = i - 1
            while j >= 0 and not is_size(tokens[j]):
                color[j] = True
                j -= 1
    for i, word in enumerate(words):
        if word in _COLOR_GLUE and 0 < i < len(words) - 1 and color[i - 1] and color[i + 1]:
            color[i] = True
    picked = [t for t, c in zip(tokens, color) if c]
    rest = [t for t, c in zip(tokens, color) if not c]
    while rest and rest[0].casefold() in _COLOR_GLUE:
        rest.pop(0)
    return " ".join(picked), " ".join(rest)


def apply_nemus_groups(items: list[NormalizedItem], nemus: dict) -> None:
    """nemus neturi variantų: tas pats ženklas + pavadinimas iki paskutinio kablelio -> viena prekė.

    Variantas tik ne spalva (spalva lieka pavadinime: kitos spalvos – kita prekė). Prekė su vienu
    dydžiu irgi gauna grupę – kitas dydis vėliau prisijungs prie tos pačios. Kodai su ta pačia
    reikšme nejungiami; gamintojo / PS grupės lieka pirmesnės."""
    from django.utils.text import slugify

    groups: dict[tuple[str, str], list[tuple[NormalizedItem, str, str]]] = {}
    for item in items:
        content = nemus.get(item.external_id.upper())
        if content is None or item.extra.get("product_group"):
            continue
        base, value = split_variant_name(content.series or "")
        color, value = split_color(value)
        if color and value:
            base = f"{base}, {color}"
        if value:
            key = (slugify(item.brand_name or ""), base.casefold())
            groups.setdefault(key, []).append((item, base, value))
    for (brand, key), members in groups.items():
        counts = Counter(value.casefold() for _, _, value in members)
        members = [m for m in members if counts[m[2].casefold()] == 1]
        if len(members) == 1 and not is_size(members[0][2]):
            continue
        if not members:
            continue
        base = members[0][1]
        digest = hashlib.sha1(f"{brand}|{key}".encode()).hexdigest()[:8]
        group = f"{NEMUS}:{slugify(base)[:60]}-{digest}"
        for item, _, value in members:
            item.extra["product_group"] = group
            item.extra["base_name"] = base
            axes = dict((item.attributes or {}).get("axes") or {})
            axes.setdefault("size", value)
            item.attributes = {**(item.attributes or {}), "axes": axes}


def manufacturer_lookup(items: list[NormalizedItem]) -> dict[tuple[str, str], Any]:
    """(ženklo slug, kodas) -> ManufacturerContent: pirma EN, tada mažiausias puslapis."""
    from django.utils.text import slugify

    from apps.suppliers.models import ManufacturerContent

    sources = ({slugify(item.brand_name or "") for item in items} - {""}) | set(RETAIL_SOURCES)
    rank = {"en": 0, "de": 1}
    best: dict[tuple[str, str], Any] = {}
    for content in ManufacturerContent.objects.filter(source__in=sources).exclude(codes=[]):
        key_rank = (rank.get(content.language, 2), len(content.codes), content.url)
        for code in content.codes:
            current = best.get((content.source, code))
            if current is None or key_rank < current[0]:
                best[(content.source, code)] = (key_rank, content)
    return {key: value[1] for key, value in best.items()}


def apply_manufacturer_content(items: list[NormalizedItem], lookup: dict) -> None:
    """Gamintojo puslapis = bazinė prekė: product_group, base_name, ašys, nuotraukos.

    Kodai be gamintojo puslapio lieka atskiromis prekėmis su Stock pavadinimu ir FOTO."""
    from django.utils.text import slugify

    for item in items:
        source = slugify(item.brand_name or "")
        content = lookup.get((source, item.external_id.upper()))
        if content is None:
            retail = [
                lookup[(source, item.external_id.upper())]
                for source in RETAIL_SOURCES
                if (source, item.external_id.upper()) in lookup
            ]
            _apply_retail(item, next((c for c in retail if c.images), retail[0] if retail else None))
            continue
        slug = content.url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html")
        item.extra["product_group"] = f"{source}:{slug}"
        item.extra["manufacturer_url"] = content.url
        if content.series:
            item.extra["base_name"] = content.series
        section = content.section_for(item.external_id.upper()) or {}
        row = dict((section.get("rows") or {}).get(item.external_id.upper()) or {})
        row.pop("name", None)
        if row:
            item.attributes = {**(item.attributes or {}), "axes": row}
        ean = (content.specs or {}).get("eans", {}).get(item.external_id.upper())
        if ean and not item.ean:
            item.ean = ean
        images = list(dict.fromkeys([*item.images, *content.images]))
        item.images = images
        if images and not item.extra.get("main_image_url"):
            item.extra["main_image_url"] = images[0]
    # Tas pats EAN keliems kodams (šaltinio klaida) -> pipeline sujungtų skirtingas prekes; nesiunčiam.
    ean_codes: dict[str, set[str]] = {}
    for item in items:
        if item.ean:
            ean_codes.setdefault(item.ean, set()).add(item.external_id)
    for item in items:
        if item.ean and len(ean_codes[item.ean]) > 1:
            item.ean = None


def _apply_retail(item: NormalizedItem, content) -> None:
    """Mažmenininko (nemus.lt, zuklys.lt) puslapis: tik nuotraukos ir parametrai, be grupavimo."""
    if content is None:
        return
    item.extra["retail_url"] = content.url
    section = content.section_for(item.external_id.upper()) or {}
    specs = dict((section.get("rows") or {}).get(item.external_id.upper()) or {})
    if specs:
        item.attributes = {**(item.attributes or {}), "specs": specs}
    item.images = list(dict.fromkeys([*item.images, *content.images]))
    if item.images and not item.extra.get("main_image_url"):
        item.extra["main_image_url"] = item.images[0]


def apply_photo_groups(items: list[NormalizedItem]) -> None:
    """Kodai be gamintojo puslapio su ta pačia Stock FOTO, ženklu ir skyriumi -> viena prekė.

    Jungiama tik jei bendra pavadinimo pradžia ilgesnė už ženklą (kitaip FOTO bendra kategorijai).
    'DAM Damyl Tectan Hyper 4Oz 1.200M 0.30mm' / '... 860M 0.35mm' -> base 'DAM Damyl Tectan Hyper 4Oz'."""
    from django.utils.text import slugify

    groups: dict[tuple[str, str, str], list[NormalizedItem]] = {}
    for item in items:
        photo = item.extra.get("photo_group")
        if photo and not item.extra.get("product_group") and item.brand_name:
            key = (slugify(item.brand_name), item.extra.get("category_raw", ""), photo)
            groups.setdefault(key, []).append(item)
    for (source, section, photo), members in groups.items():
        if len(members) < 2:
            continue
        tokens = [item.name.split() for item in members]
        prefix = []
        for column in zip(*tokens):
            if len({t.lower() for t in column}) > 1:
                break
            prefix.append(column[0])
        brand_len = len((members[0].brand_name or "").split())
        if len(prefix) <= brand_len:
            continue
        digest = hashlib.sha1(f"{section}|{photo}".encode()).hexdigest()[:12]
        group = f"{source}:photo:{digest}"
        for item in members:
            item.extra["product_group"] = group
            item.extra["base_name"] = " ".join(prefix)


def apply_fixed_groups(items: list[NormalizedItem], groups: dict) -> None:
    """Feed config `fixed_groups` {kodas: grupė} – rankinis (PS) grupavimas, svarbiausias."""
    fixed = {str(k).strip().upper(): v for k, v in groups.items()}
    for item in items:
        value = fixed.get(item.external_id.upper())
        if not value:
            continue
        if isinstance(value, dict):
            item.extra["product_group"] = str(value["group"])
            if value.get("base_name"):
                item.extra["base_name"] = value["base_name"]
        else:
            item.extra["product_group"] = str(value)


def _distinguish_twins(items: list[NormalizedItem]) -> None:
    """Grupės variantams su vienodomis ašimis `model` = Stock pavadinimo skirtumas.

    'Savage Gear Exorus 1000 7bb 5.2:1 210g' / '... 2500 7bb 5.2:1 260g' -> '1000 210g' / '2500 260g'."""
    groups: dict[str, list[NormalizedItem]] = {}
    for item in items:
        group = item.extra.get("product_group")
        if group:
            groups.setdefault(group, []).append(item)
    for members in groups.values():
        same: dict[str, list[NormalizedItem]] = {}
        for item in members:
            axes = (item.attributes or {}).get("axes") or {}
            same.setdefault(json.dumps(axes, sort_keys=True), []).append(item)
        for twins in same.values():
            if len(twins) < 2:
                continue
            tokens = [[t for t in re.split(r"\s+", item.name) if t] for item in twins]
            common = set.intersection(*({t.lower() for t in toks} for toks in tokens))
            for item, toks in zip(twins, tokens):
                label = " ".join(t for t in toks if t.lower() not in common) or item.external_id
                axes = dict((item.attributes or {}).get("axes") or {})
                axes["model"] = f"{axes['model']} {label}" if axes.get("model") else label
                item.attributes = {**(item.attributes or {}), "axes": axes}
