"""Albatros (AL.) – vietinis tiekėjas, „Centrinis sandėlis“.

Šaltinis – pilnas Stock Excel (`Stock 10.21.xlsx`): skyriaus eilutės (užpildytas tik A)
ir prekių eilutės `A žymė | Code | Item | be PVM, export | SALE | Q-ty | Order | FOTO`.
Planas ir sprendimai: `albatros_import.md`.

Kainodara (vartotojo sprendimas):
    cost     = export × (1 + SALE)      -> purchase_price (BE cost_eur)
    price    = export × 1,5             -> rrp_price (be PVM; pricing engine ima RRP bazę)
    discount = round(−SALE × 100)       -> discount_percent (BE offer_discount_percent)
"""
from __future__ import annotations

import io
import logging
import re
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from openpyxl import load_workbook

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter

logger = logging.getLogger(__name__)

PRICE_FACTOR = Decimal("1.5")
CENT = Decimal("0.01")

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
    ("LUCKY CRAFT", "Lucky Craft"), ("MINN KOTA", "Minn Kota"), ("OLD TOWN", "Old Town"),
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
        for row in self.parse(payload):
            try:
                yield self.normalize(row)
            except AdapterError as exc:
                self.fetch_errors += 1
                logger.warning("%s", exc)

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
