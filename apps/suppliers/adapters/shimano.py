from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter

_MEAS = r"\d+(?:[.,]\d+)?"

# Nuotraukų URL bazė (spec instructions.md)
_PICTURE_BASE = "http://www.shimanoshop-eu.com/Shop/Artikel_KenmerkOptie/"

# desc2 token -> ašis. Tvarka svarbi: ilgesni/konkretesni šablonai pirmi.
_TOKEN_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Ritės dydis: 'C2500S', '2500HG', 'C3000M', 'C5000' – 'C'/'D' raidė
    # arba >=4 skaitmenys (3-skaitmeniai '008' yra color kodai)
    (re.compile(r"^([CcDd]\d{3,5}[A-Z]{0,3}|\d{4,5}[A-Z]{0,3})$"), "reel_size"),
    # Perdavimo greitis: 'XG', 'HG', 'MHG', 'SXG', 'PG'
    (re.compile(r"^(?:S?XG|H?H?G|MHG|PG|HG)$"), "gear_ratio"),
    # Ritės tipas pirmame žodyje
    (re.compile(r"^(?:Spin|Spinning|Cast|Casting|Tele|Fly)$", re.I), "rod_type"),
    # Veiksmas (action)
    (
        re.compile(r"^(?:EX-FAST|XFAST|FAST|MEDIUM|MODERATE|SLOW|PARABOLIC)$", re.I),
        "action",
    ),
    # Skaičius sekcijų: '2pc', '1+1pc', '4pc'
    (re.compile(r"^(\d+(?:\+\d+)?)\s*pc\.?$", re.I), "sections"),
    # EGI diapazonas: 'EGI:1.8-3.8'
    (re.compile(r"^EGI:" + _MEAS + r"(?:-" + _MEAS + r")?$", re.I), "egi"),
    # Imperialus ilgis: 8'3" / 5'5"
    (re.compile(r"^\d+'\d+\"?$"), "length_imperial"),
    # Metrinis ilgis: 2,70m / 2.40m / 50m (meškerė/valas)
    (re.compile(r"^(" + _MEAS + r")m$", re.I), "length"),
    # Masalo/voblerio ilgis: 85mm / 220mm (be kablelio arba >=1)
    (re.compile(r"^(" + _MEAS + r")mm$", re.I), "size"),
    # Metimo svoris: 12-28g / 30-90g (diapazonas)
    (re.compile(r"^(" + _MEAS + r")-(" + _MEAS + r")g$", re.I), "casting_weight"),
    # Svoris: 87g / 150g
    (re.compile(r"^(" + _MEAS + r")g$", re.I), "weight"),
    # Stiprumas kg: 11,93kg
    (re.compile(r"^(" + _MEAS + r")kg$", re.I), "strength"),
    # Stiprumas lb: 80lb / 8-16 lbs / 30-50lbs
    (re.compile(r"^(" + _MEAS + r")-?(" + _MEAS + r")?lbs?$", re.I), "strength_lb"),
    # Valo ilgis: '150m', '300m' – tas pats 'length' regex aukščiau
]

# Skliaustų/uodegos 'section N' rod sections
_SECTION_RE = re.compile(r"^(.*?)\s+section\s+(\d+)\s*$", re.I)

# Spalvų žodynas (kaip korda) – liekana su spalvos žodžiu -> 'color'
_COLOR_WORDS = {
    "red", "green", "blue", "yellow", "black", "white", "grey", "gray",
    "purple", "camo", "olive", "brown", "pink", "orange", "silver",
    "gold", "charcoal", "navy", "burgundy", "teal", "khaki", "beige",
    "cream", "ivory", "turquoise", "magenta", "shad", "shiner", "chart",
    "chartreuse", "ghost", "clown", "mirror", "clear", "ayu", "chiayu",
    "sexy", "sunny", "smelt", "wakasagi", "sardine", "anchovy", "mackerel",
    "pearl", "neon", "fire", "tiger", "zebra", "rainbow", "luminous",
    "multi",
}

_SPARE_ITEMGROUPS = {
    "reel parts",
    "spools",
    "aspire pole sections",
    "carp cruncher pole sections",
    "catana pole sections",
    "exage pole sections",
    "fireblood pole sections",
    "generic pole kits",
    "rod sections (excl poles)",
    "diaflash pole sections",
}


def _parse_desc2(desc2: str, code: str = "") -> tuple[dict[str, str], str]:
    """'85mm 008 St Sexy Shad' -> {size, color}; 'Spin 2.70m 12-28g 2pc' ->
    {rod_type, length, casting_weight, sections}. Liekana -> color/variantas.
    Prekės kodas desc2 viduje ('59VZNM70T08 AYU') pašalinamas.
    Grąžina (ašys, desc2-be-kodo)."""
    axes: dict[str, str] = {}
    leftover: list[str] = []
    kept: list[str] = []
    code_l = code.lower()
    tokens = desc2.split()
    # Multi-token '8-16 lbs': sujungiama prieš match'inimą
    merged: list[str] = []
    for token in tokens:
        if merged and re.fullmatch(r"lbs?\.?", token, re.I) and re.fullmatch(
            _MEAS + r"(?:-" + _MEAS + r")?", merged[-1]
        ):
            merged[-1] = merged[-1] + "lb"
            continue
        merged.append(token)
    for token in merged:
        if code_l and token.lower() == code_l:
            continue
        kept.append(token)
    for token in kept:
        matched = False
        for pattern, axis in _TOKEN_PATTERNS:
            m = pattern.match(token)
            if m:
                if axis in ("size", "line_diameter"):
                    # 0,40mm -> line_diameter; 85mm -> size
                    value = m.group(1)
                    try:
                        numeric = float(value.replace(",", "."))
                        axis = "line_diameter" if numeric < 1 else "size"
                    except ValueError:
                        pass
                    axes[axis] = m.group(0)
                elif axis == "casting_weight":
                    axes[axis] = f"{m.group(1)}-{m.group(2)}g"
                elif axis == "strength_lb":
                    axes[axis] = m.group(0).lower()
                else:
                    axes[axis] = m.group(0)
                matched = True
                break
        if not matched:
            leftover.append(token)
    if leftover:
        rest = " ".join(leftover).strip()
        if re.match(r"^\d{3}\b", rest) or any(
            w.lower() in _COLOR_WORDS for w in leftover
        ):
            axes.setdefault("color", rest)
        else:
            axes.setdefault("variantas", rest)
    return axes, " ".join(kept)


# Galinis 3-skaitmenis varianto/spalvos indeksas: 'Flügel Flat 70 012' ->
# 'Flügel Flat 70'. 2-skaitmenis modelio nr ('Flat 70') lieka.
_INDEX_RE = re.compile(r"^(.*?)\s+\d{3}$")


def _clean_desc1(desc1: str, desc2: str = "") -> tuple[str, str]:
    """'YASPIC26 section 1' -> ('YASPIC26', '1').
    'Flügel Flat 70 012' -> ('Flügel Flat 70', '') kai desc2 ne tuščias."""
    d1 = desc1.strip()
    m = _SECTION_RE.match(d1)
    if m:
        return m.group(1).strip(), m.group(2)
    if desc2.strip():
        mi = _INDEX_RE.match(d1)
        if mi:
            return mi.group(1).strip(), ""
    return d1, ""


def _code_stem(code: str) -> str:
    """'Z3922ZE006' -> 'Z3922ZE'; '59VZNM70T08' -> '59VZNM70T';
    'SHMLKMBY' (be galinių skaitmenų) -> ''."""
    stem = re.sub(r"\d+$", "", code.strip())
    return stem if stem != code.strip() and len(stem) >= 4 else ""


@register_adapter
class ShimanoAdapter(BaseAdapter):
    """Shimano ArtExpPLNF_Baltic.txt (';' CSV, latin-1).

    Grupavimas: (brand, desc1-be-'section N') -> variantai; ašys iš desc2
    token'ų. Atsarginės dalys (tuščias desc2) -> variantless produktai.
    """

    code = "shimano"

    def fetch(self, uploaded_path: str | None = None) -> bytes:
        if uploaded_path or self.feed.file:
            return super().fetch(uploaded_path)
        # ftp://host/path + config username/password
        parsed = urlparse(self.feed.url)
        if parsed.scheme == "ftp":
            from ftplib import FTP

            ftp = FTP(parsed.hostname, timeout=int(self.config.get("timeout", 60)))
            ftp.login(
                self.config.get("username") or parsed.username or "",
                self.config.get("password") or parsed.password or "",
            )
            ftp.set_pasv(True)
            buf = io.BytesIO()
            ftp.retrbinary("RETR " + parsed.path.lstrip("/"), buf.write)
            ftp.quit()
            return buf.getvalue()
        return super().fetch()

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = payload.decode("latin-1")
        reader = csv.reader(io.StringIO(text), delimiter=";")
        header = next(reader, None)
        if not header:
            return
        items = []
        for row in reader:
            if len(row) < 17:
                continue
            cells = [c.strip() for c in row]
            code = cells[1] or cells[2]  # Shimanoitem == 3rd item nr
            if not code:
                continue
            desc1 = cells[4]
            desc2 = cells[5]
            ean = cells[3]
            brand = cells[13] or "Shimano"
            picture = cells[14]
            itemgroup = cells[12]
            base_desc1, section = _clean_desc1(desc1, desc2)
            axes, desc2_clean = _parse_desc2(desc2, code) if desc2 else ({}, desc2)
            if section:
                axes["section"] = section
            items.append(
                {
                    "short_item_no": cells[0],
                    "code": code,
                    "ean": ean,
                    "desc1": desc1,
                    "desc2": desc2_clean,
                    "base_desc1": base_desc1,
                    "section": section,
                    "product_group": "",
                    "axes": axes,
                    "price": self._to_decimal(cells[7]),
                    "sales_price": self._to_decimal(cells[8]),
                    "cnv": cells[10],
                    "stock_flag": cells[11],
                    "itemgroup": itemgroup,
                    "brand": brand,
                    "picture": picture,
                    "raw": cells,
                }
            )

        # Barkodų dublikatai (kaip Korda): pirmasis laimi
        seen_eans: set[str] = set()
        for item in items:
            ean = item.get("ean") or ""
            if ean and ean in seen_eans:
                item["duplicate_ean"] = ean
                item["ean"] = ""
            elif ean:
                seen_eans.add(ean)

        # Grupavimas dviejuose etapuose:
        # 1) kodų stem'as (be galinių skaitmenų): 'Z3922ZE006'+'Z3922ZE005'
        #    -> 'Z3922ZE'. Tai Shimano šeimos konvencija.
        # 2) fallback (brand, base_desc1) — kodams be bendro stem'o
        #    (pvz. akiniai 'SHMLKMBY'/'SHMMCMBBM').
        # Parts (be ašių / spare itemgroup) -> variantless.
        stem_groups: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            stem = _code_stem(item["code"])
            if stem:
                stem_groups.setdefault(stem.lower(), []).append(item)
        desc_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for stem_l, members in stem_groups.items():
            if len(members) == 1:
                m = members[0]
                key = (m["brand"].lower(), m["base_desc1"].lower())
                desc_groups.setdefault(key, []).append(m)
        for item in items:
            if not _code_stem(item["code"]):
                key = (item["brand"].lower(), item["base_desc1"].lower())
                desc_groups.setdefault(key, []).append(item)
        groups = [m for m in stem_groups.values() if len(m) > 1]
        groups += [m for m in desc_groups.values() if len(m) > 1]
        for members in groups:
            has_axes = any(m["axes"] for m in members)
            is_spare = (
                members[0]["itemgroup"].lower() in _SPARE_ITEMGROUPS
                or not has_axes
            )
            group_code = (
                _group_code([m["code"] for m in members])
                if has_axes and not is_spare
                else ""
            )
            if group_code:
                combos: dict[tuple, list[dict[str, Any]]] = {}
                for m in members:
                    combos.setdefault(
                        tuple(sorted(m["axes"].items())), []
                    ).append(m)
                for combo, same in combos.items():
                    if len(same) > 1:
                        for m in same:
                            m["axes"]["variantas"] = m["code"]
            for m in members:
                m["product_group"] = group_code
        yield from items

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        code = self._text(row.get("code"))
        if not code:
            raise AdapterError(f"Shimano: trūksta kodo: {repr(row)[:200]}")
        desc1 = self._text(row.get("desc1"))
        desc2 = self._text(row.get("desc2"))
        name = f"{desc1} {desc2}".strip() or code
        brand = self._text(row.get("brand")) or "Shimano"
        base_desc1 = self._text(row.get("base_desc1")) or desc1
        product_group = self._text(row.get("product_group"))
        # base_name tik grupuotoms prekėms; parts -> '' (variantless)
        base_name = ""
        if product_group:
            base_name = base_desc1
            if brand and brand.lower() not in base_name.lower():
                base_name = f"{brand} {base_name}"
        picture = self._text(row.get("picture"))
        images = [_PICTURE_BASE + picture] if picture else []
        stock_flag = self._text(row.get("stock_flag")).upper()
        in_stock_qty = int(self.config.get("in_stock_qty") or 99)
        stock = in_stock_qty if stock_flag == "Y" else 0
        cnv = self._to_int(row.get("cnv"))
        itemgroup = self._text(row.get("itemgroup"))
        axes = {k: v for k, v in (row.get("axes") or {}).items() if v}
        extra = {
            "identifiers": {
                "art_no": code,
                "product_group": product_group,
                "short_item_no": self._text(row.get("short_item_no")),
                "trade_pack_qty": cnv if cnv > 1 else None,
            },
            "base_name": base_name,
            "product_group": product_group,
            "duplicate_ean": self._text(row.get("duplicate_ean")),
            "stock_flag": stock_flag,
            "rrp_price": row.get("sales_price"),
            "trade_pack_qty": cnv if cnv > 1 else None,
        }
        extra = _clean(extra)
        return NormalizedItem(
            external_id=code,
            sku=code,
            name=name,
            ean=self._text(row.get("ean")),
            brand_name=brand,
            category_path=itemgroup if itemgroup != "." else "Uncategorized",
            description=name,
            attributes={"axes": axes} if axes else {},
            images=images,
            stock=stock,
            purchase_price=row.get("price"),
            currency="EUR",
            is_available=stock_flag == "Y",
            extra={k: v for k, v in extra.items() if v not in (None, "")},
            raw=_clean(row),
        )


def _group_code(codes: list[str]) -> str:
    """Bendras ilgiausias prefiksas, min 4 simboliai (pvz. 'Z3M85ZE0')."""
    codes = sorted(codes)
    prefix = codes[0]
    for c in codes[1:]:
        while not c.startswith(prefix) and prefix:
            prefix = prefix[:-1]
    return prefix if len(prefix) >= 4 else codes[0]


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    return value
