from __future__ import annotations

import io
import re
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from openpyxl import load_workbook

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter

# Ašys, atpažįstamos iš Description galo (spec .agent.md).
# (regex_tail, axis). Ilgesni šablonai – pirmi.
_SIZE_LETTER = r"(?:XXXXXL|XXXXL|XXXL|XXL|XL|XS|S|M|L)"
_SIZE_WORD = r"(?:Super\s+Mini|Mini|X-Small|Small|Medium|Large|Extra\s+Large)"

_MEAS = r"\d+(?:[.,]\d+)?"

_TAIL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"^({_SIZE_WORD}\s+{_MEAS}\s*g)$", re.I), "size"),
    (re.compile(rf"^({_MEAS}\s*oz\s+(?:large|medium|small))$", re.I), "size"),
    (re.compile(r"^(UK\s*\d+\s*/\s*EU\s*\d+)$", re.I), "size"),
    (re.compile(r"^(Size\s*\d+)$", re.I), "size"),
    (re.compile(rf"^({_SIZE_LETTER})$", re.I), "size"),
    # 'Heli Swivel Large', 'Window Feeder Medium' – vienažodis dydis
    (re.compile(rf"^({_SIZE_WORD})$", re.I), "size"),
    # '6oz / 168 gr.', '5kg' – galima dviguba vieneto forma
    (
        re.compile(
            rf"^({_MEAS}\s*(?:g|gr\.?|kg|oz))(?:\s*[/x×]\s*{_MEAS}\s*(?:g|gr\.?|kg|oz))*$",
            re.I,
        ),
        "weight",
    ),
    (re.compile(rf"^({_MEAS}\s*mm)$", re.I), "line_diameter"),
]

# Matavimo-uodegos -> 'variantas' ašis (neaiškūs, pvz. '0.7mm 7m', '70lb 15m')
_MEAS_UNIT = (
    r"mm|cm|m\b|kg|g\b|gr\.?|lb|lbs|oz|in|inch|ft|\"|'|yds?|litre|l|ml|pcs"
)
_VARIANTAS_TAIL = re.compile(
    rf"({_MEAS}\s*(?:{_MEAS_UNIT}))(?:[\s/]+{_MEAS}\s*(?:{_MEAS_UNIT}))*$",
    re.I,
)

_TRAILING_PAREN_RE = re.compile(r"\s*\(([^()]*)\)\s*$")

# Spalvų žodynas – '|' uodegos liekana / skliaustų turinys -> 'color' ašis
_COLOR_WORDS = {
    "red", "green", "blue", "yellow", "black", "white", "grey", "gray",
    "purple", "camo", "kamo", "olive", "brown", "pink", "orange",
    "silver", "gold", "charcoal", "navy", "burgundy", "teal", "khaki",
    "beige", "cream", "ivory", "turquoise", "magenta",
}

# Žodžiai prieš spalvą, sudarantys spalvinę frazę ('Dark Olive', 'Light Grey')
_SHADE_MODIFIERS = {
    "dark", "light", "bright", "deep", "pale", "matte", "matt",
    "fluoro", "heather", "charcoal",
}

# Headgroup'ai, kurių neimportuojame
_SKIP_HEADGROUPS = {"team / marketing"}

# Papildomo produktų-info failo (pvz. carpporter.xlsx) numatytoji kalba
_INFO_DEFAULT_SHEET = "EN"


def _md_description(full: str, features: str) -> str:
    """Full description (pastraipos) + Key features (md sąrašas)."""
    parts = [full.strip()] if full and full.strip() else []
    bullets = [
        line.strip() for line in re.split(r"\n+", features or "") if line.strip()
    ]
    if bullets:
        parts.append("\n".join(f"- {b}" for b in bullets))
    return "\n\n".join(parts)


def read_product_info(path: Any, sheet_name: str = "") -> dict[str, dict[str, Any]]:
    """Korda product-info xlsx -> {ref: {name, description, images, nb_image}}.

    Lapai pavadinti kalbos kodais (EN/NL/FR/...). 'Also applies to' stulpelyje
    išvardyti kodai gauna tą pačią info (tas pats produktas kitame feed'e).
    """
    workbook = load_workbook(path, read_only=True, data_only=True)
    names = workbook.sheetnames
    sheet = (
        workbook[sheet_name]
        if sheet_name and sheet_name in names
        else workbook[_INFO_DEFAULT_SHEET if _INFO_DEFAULT_SHEET in names else names[0]]
    )
    rows = sheet.iter_rows(values_only=True)
    header = [str(h or "").strip() for h in next(rows, [])]
    col = {h: i for i, h in enumerate(header)}

    def cell(row: tuple, name: str) -> str:
        i = col.get(name)
        return str(row[i]).strip() if i is not None and row[i] is not None else ""

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        ref = cell(row, "Product Ref")
        if not ref:
            continue
        pictures = [
            url.strip() for url in re.split(r"[\s]+", cell(row, "Pictures")) if url.strip()
        ]
        record = {
            "name": cell(row, "Product Name"),
            "description": _md_description(
                cell(row, "Full description"), cell(row, "Key features")
            ),
            "images": pictures,
            "nb_image": cell(row, "Nb Image"),
            "headgroup": cell(row, "Headgroup"),
            "subgroup": cell(row, "Subgroup"),
        }
        out[ref] = record
        for other in re.split(r"[\s,;/]+", cell(row, "Also applies to") or ""):
            if other.strip():
                out.setdefault(other.strip(), record)
    return out


def _pop_color(text: str) -> tuple[str, str]:
    """Uodegos galas iš spalvų žodyno -> ('likutis', 'Dark Olive').
    Jei spalva nerasta -> ('tekstas', '')."""
    words = text.split()
    if not words or words[-1].lower() not in _COLOR_WORDS:
        return text, ""
    if len(words) > 1 and words[-2].lower() in _SHADE_MODIFIERS:
        color, rest = f"{words[-2]} {words[-1]}", " ".join(words[:-2])
    else:
        color, rest = words[-1], " ".join(words[:-1])
    return rest.strip(" -–"), color.title()


def _trailing_parens(text: str) -> tuple[str, str]:
    """'Name (A) (B)' -> ('Name', 'B A' – skliaustų turiniai)."""
    parens: list[str] = []
    while True:
        match = _TRAILING_PAREN_RE.search(text)
        if not match:
            break
        parens.insert(0, match.group(1).strip())
        text = text[: match.start()]
    return text.strip(), " ".join(p for p in parens if p)


def _split_tail(tail: str) -> tuple[dict[str, str], str]:
    """Uodegos tekstas -> ({axis: value}, neatpažinta liekana).

    'Grey XS' -> ({size: XS}, 'Grey' -> color); 'Barbed 8' -> ({}, 'Barbed 8').
    """
    axes: dict[str, str] = {}
    words = tail.strip().split()
    for n in (3, 2, 1):
        if len(words) < n:
            continue
        candidate = " ".join(words[-n:])
        for pattern, axis in _TAIL_PATTERNS:
            if pattern.match(candidate):
                axes[axis] = candidate
                leftover = " ".join(words[:-n]).strip(" -–")
                return axes, leftover
    return {}, tail.strip()


def _parse_description(description: str) -> tuple[str, dict[str, str], str]:
    """Description -> (base, {axis: value}, parens).

    'a | b' uodega -> žinomi uodegos šablonai -> matavimo uodega
    ('variantas') -> nėra option. Skliaustai gale ignoruojami; liekana
    iš spalvų žodyno -> 'color'.
    """
    clean, parens = _trailing_parens(description.strip())
    axes: dict[str, str] = {}
    if "|" in clean:
        head, _, tail = clean.rpartition("|")
        axes, leftover = _split_tail(tail)
        leftover, color = _pop_color(leftover)
        if color:
            axes["color"] = color
        if leftover:
            # '| 14' plikas skaičius – kabliuko/dalys dydis
            if re.fullmatch(r"\d{1,3}", leftover):
                axes["size"] = leftover
            else:
                axes.setdefault("variantas", leftover)
        # Skliaustų turinys – jei matavimo šablonas, tai atskira ašis:
        # 'F1 Pellet Pole Rigs 6" | 14 (0.15mm)' -> size + line_diameter
        if parens:
            p_axes, _ = _split_tail(parens)
            for ax, val in p_axes.items():
                axes.setdefault(ax, val)
        return head.strip(), axes, parens
    words = clean.split()
    # Žinomos uodegos formos – ilgiausia pirma ('Super Mini 20g' ir pan.)
    for n in (4, 3, 2, 1):
        if len(words) <= n:
            continue
        tail = " ".join(words[-n:])
        for pattern, axis in _TAIL_PATTERNS:
            if pattern.match(tail):
                axes[axis] = tail
                base = " ".join(words[:-n]).strip(" -–")
                base, color = _pop_color(base)
                if color:
                    axes["color"] = color
                if parens:
                    p_axes, _ = _split_tail(parens)
                    for ax, val in p_axes.items():
                        axes.setdefault(ax, val)
                return base, axes, parens
    match = _VARIANTAS_TAIL.search(clean)
    if match and match.group(0) != clean:
        return (
            clean[: match.start()].strip(" -–"),
            {"variantas": match.group(0).strip()},
            parens,
        )
    return clean, axes, parens


def _trade_pack_qty(text: Any) -> int:
    """'10 in trade pack' -> 10, 'per piece'/'piece' -> 1,
    'per piece, sold per 5' -> 5."""
    raw = str(text or "").strip().lower()
    if not raw:
        return 1
    sold_per = re.search(r"sold\s+per\s+(\d+)", raw)
    if sold_per:
        return int(sold_per.group(1))
    numbers = re.findall(r"\d+", raw)
    return int(numbers[0]) if numbers else 1


def _group_code(codes: list[str]) -> str:
    """'pirmas kodas' + '-' + paskutinio kodo skaitmenys (GF079-83)."""
    if len(codes) == 1:
        return codes[0]
    digits = re.search(r"(\d+)\D*$", codes[-1])
    tail = digits.group(1)[-2:] if digits else codes[-1][-2:]
    return f"{codes[0]}-{tail}"


@register_adapter
class KordaAdapter(BaseAdapter):
    code = "korda"

    def fetch(self, uploaded_path: str | None = None) -> bytes:
        if uploaded_path or self.feed.file:
            return super().fetch(uploaded_path)
        import requests

        login_url = self.config.get("login_url") or "https://team.kordashop.net/login"
        username = self.config.get("username", "")
        password = self.config.get("password", "")
        timeout = int(self.config.get("timeout", 60))
        session = requests.Session()
        if username:
            page = session.get(login_url, timeout=timeout)
            page.raise_for_status()
            token = re.search(
                r'name="_token" value="([^"]+)"', page.text
            )
            form = {"email": username, "identity": username, "password": password}
            if token:
                form["_token"] = token.group(1)
            action = re.search(r'<form[^>]*action="([^"]+)"', page.text)
            post_url = action.group(1) if action else "/user/login"
            if post_url.startswith("/"):
                from urllib.parse import urljoin

                post_url = urljoin(login_url, post_url)
            login = session.post(post_url, data=form, timeout=timeout)
            login.raise_for_status()
        response = session.get(self.feed.url, timeout=timeout)
        response.raise_for_status()
        return response.content

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        sheet = workbook["Current products"] if "Current products" in workbook.sheetnames else workbook.active
        rows = sheet.iter_rows(values_only=True)
        header = [str(h or "").strip() for h in next(rows, [])]

        items: list[dict[str, Any]] = []
        for row in rows:
            record = {header[i]: row[i] for i in range(min(len(header), len(row)))}
            headgroup = self._text(record.get("Headgroup"))
            if headgroup.lower() in _SKIP_HEADGROUPS:
                continue
            # Tuščias Headgroup feed'e – atskiras path, kad būtų ką mapinti
            if not headgroup:
                headgroup = "Uncategorized"
            code = self._text(record.get("Code"))
            description = self._text(record.get("Description"))
            if not code or not description:
                continue
            rrp = self._to_decimal(record.get("RRP"))
            if rrp is None:
                continue  # spec: tuščia/ne skaičius RRP -> neimportuoti
            items.append(
                {
                    "code": code,
                    "brand": self._text(record.get("Brand")),
                    "description": description,
                    "headgroup": headgroup,
                    "subgroup": self._text(record.get("Subgroup")),
                    "selling": self._to_decimal(record.get("Selling price")),
                    "rrp": rrp,
                    "trade_pack_size_raw": self._text(record.get("Trade Pack Size")),
                    "trade_pack_qty": _trade_pack_qty(record.get("Trade Pack Size")),
                    "trade_pack_price": self._to_decimal(record.get("Trade Pack Price")),
                    "barcode": self._text(record.get("Barcode")),
                    "stock_text": self._text(record.get("Stock")),
                    "commodity_code": self._text(record.get("Commodity code")),
                    "country_of_origin": self._text(record.get("Country of origin")),
                    "weight_kg": self._to_decimal(record.get("Weight")),
                }
            )

        # Papildomas produktų-info failas (config info_file) – description/images.
        info = self._product_info()
        for item in items:
            rec = info.get(item["code"])
            if rec:
                item["info_description"] = rec["description"]
                item["info_images"] = rec["images"]
                item["info_nb_image"] = rec["nb_image"]

        # Feed'e pasitaiko tų pačių barkodų kelioms prekėms
        # (pvz. *COMP kompensacinės eilutės). Shop reikalauja unikalumo –
        # barkodą paliekame pirmam, likusieji -> duplicate_barcode.
        seen_barcodes: set[str] = set()
        for item in items:
            barcode = item.get("barcode") or ""
            if not barcode:
                continue
            if barcode in seen_barcodes:
                item["duplicate_barcode"] = barcode
                item["barcode"] = ""
            else:
                seen_barcodes.add(barcode)

        # Grupavimas: vienoda (brand, base be option) -> variantai
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in items:
            base, axes, parens = _parse_description(item["description"])
            item["base"] = base
            item["axes"] = axes
            item["parens"] = parens
            key = (item["brand"].lower(), base.lower())
            groups.setdefault(key, []).append(item)

        for members in groups.values():
            members.sort(key=lambda i: i["code"])
            group_code = _group_code([i["code"] for i in members])
            # Option kolizijos: nariai su identiškom ašim – bandome
            # atskirti skliaustų turiniu (color žodynui -> color,
            # kitaip -> variantas)
            combos: dict[tuple, list[dict[str, Any]]] = {}
            for member in members:
                combo = tuple(
                    sorted((ax, v.lower()) for ax, v in member["axes"].items())
                )
                combos.setdefault(combo, []).append(member)
            for combo, same in combos.items():
                if len(same) < 2:
                    continue
                parens_set = {m["parens"].lower() for m in same}
                if len(parens_set) <= 1:
                    continue
                for member in same:
                    paren = member["parens"]
                    if not paren:
                        continue
                    _, paren_color = _pop_color(paren)
                    if paren_color:
                        member["axes"].setdefault("color", paren_color)
                    else:
                        member["axes"].setdefault("variantas", paren)
            for member in members:
                member["product_group"] = group_code
                yield member

    def _product_info(self) -> dict[str, dict[str, Any]]:
        """config info_file / info_files -> {ref: info}; failų gali nebūti – tada {}."""
        rels = self.config.get("info_files")
        if not rels:
            single = str(self.config.get("info_file") or "").strip()
            rels = [single] if single else []
        if isinstance(rels, str):
            rels = [rels]
        from pathlib import Path

        from django.conf import settings

        sheet = ""
        if getattr(self.feed, "language", None) and self.feed.language:
            sheet = (self.feed.language.code or "").upper()
        info: dict[str, dict[str, Any]] = {}
        for rel in rels:
            rel = str(rel).strip()
            if not rel:
                continue
            path = Path(rel)
            if not path.is_absolute():
                path = Path(settings.BASE_DIR) / rel
            if not path.exists():
                continue
            info.update(read_product_info(path, sheet))
        return info

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        code = self._text(row.get("code"))
        description = self._text(row.get("description"))
        if not code:
            raise AdapterError(f"Korda: trūksta Code eilutėje: {repr(row)[:200]}")
        brand = self._text(row.get("brand")).title()
        name = description
        if brand and brand.lower() not in description.lower():
            name = f"{brand} {description}"
        base = self._text(row.get("base") or description)
        base_name = base
        if brand and not base.lower().startswith(brand.lower()):
            base_name = f"{brand} {base}".strip()

        qty = int(row.get("trade_pack_qty") or 1)
        # 'Selling price' = vieneto savikaina; jei nėra -> Trade Pack Price/qty
        purchase = row.get("selling")
        if purchase is None and row.get("trade_pack_price") is not None and qty:
            purchase = (Decimal(str(row["trade_pack_price"])) / qty).quantize(
                Decimal("0.0001")
            )

        axes = {
            ax: value
            for ax, value in (row.get("axes") or {}).items()
            if value
        }

        stock_text = self._text(row.get("stock_text"))
        # Kiekio feed'e nėra – 'In stock' -> numatytas kiekis
        # (feed config 'in_stock_qty', default 99), kiti -> 0.
        in_stock_qty = int(self.config.get("in_stock_qty") or 99)
        stock = in_stock_qty if stock_text.strip().lower() == "in stock" else 0
        identifiers = {
            "art_no": code,
            "product_group": row.get("product_group", code),
            "barcode_single_ean": self._text(row.get("barcode")),
            "pack_unit": "pcs",
            "trade_pack_qty": qty if qty > 1 else None,
            "trade_pack_price": row.get("trade_pack_price"),
        }
        extra = {
            "identifiers": identifiers,
            "base_name": base_name,
            "product_group": row.get("product_group", code),
            "sub_category_raw": self._text(row.get("subgroup")),
            "commodity_code": self._text(row.get("commodity_code")),
            "country_of_origin": self._text(row.get("country_of_origin")),
            "duplicate_barcode": self._text(row.get("duplicate_barcode")),
            "info_nb_image": self._text(row.get("info_nb_image")),
            "main_image_url": (row.get("info_images") or [""])[0],
            "package_raw": self._text(row.get("trade_pack_size_raw")),
            "trade_pack_qty": qty if qty > 1 else None,
            "trade_pack_price": row.get("trade_pack_price"),
            "shipping_weight_kg": row.get("weight_kg"),
            "rrp_price": row.get("rrp"),
            "stock_text": stock_text,
        }
        extra = self._clean_dict(extra)
        return NormalizedItem(
            external_id=code,
            sku=code,
            name=name,
            ean=self._text(row.get("barcode")),
            brand_name=brand,
            category_path=self._text(row.get("headgroup")),
            description=self._text(row.get("info_description")) or name,
            attributes={"axes": axes} if axes else {},
            images=list(row.get("info_images") or []),
            stock=stock,
            purchase_price=purchase,
            currency="EUR",
            is_available=stock_text.strip().lower() != "no stock",
            extra={k: v for k, v in extra.items() if v not in (None, "")},
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

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()
