"""Atributų reikšmių parsinimas į kanoninius skaičius+vienetus (F2).

`parse_value(text, datatype, unit, axis)` -> ParsedValue:
- number/range: '0,26 mm'/'0.26'/'10-40g'/'20 lb'/'13'3"' ->
  num/num_max konvertuoti į atributo kanoninį vienetą, display lt-stiliaus,
  code = stabilus slug ('0.26'/'0,26 mm'/'0.26mm' -> '0-26-mm').
- enum/text/bool: esama normalize_option_value logika (size alias'ai, case).
- Neparsinama -> num=None + report(unparsed_value); per didelės reikšmės
  -> suspicious=True + report(suspicious_value), bet eksportuojamos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .attributes import (
    ATTRIBUTE_META,
    is_meaningful_value,
    strip_axis_prefix,
    value_code,
)
from .unmapped import report

_NUM = r"\d+(?:[.,]\d+)?"
_UNIT_TOKENS = (
    "grams", "gram", "grs", "gr", "metres", "meter", "mtr",
    "inches", "inch", "pcs", "pc",
    "mm", "cm", "g", "kg", "lbs", "lb", "oz", "ft", "in",
    "yd", "yds", "ml", "l", "m",
)
_UNIT_RE = (
    "|".join(re.escape(t) for t in _UNIT_TOKENS) + r"|gr\.|'|''|\""
)

_SINGLE_RE = re.compile(rf"^({_NUM})\s*({_UNIT_RE})?$", re.IGNORECASE)
_RANGE_RE = re.compile(
    rf"^({_NUM})\s*({_UNIT_RE})?\s*[-–—]\s*({_NUM})\s*({_UNIT_RE})?$",
    re.IGNORECASE,
)
# '2pc', '3 pcs', '2 sk', '3-tlg', '2 sections', '2-piece', '4pc+tips'
_COUNT_SUFFIX = (
    "pcs?", "sk", "składy", "sklady", "tlg", "teile",
    "sections?", "pieces?", "vnt", "dalių", "dalies",
)
_COUNT_RE = re.compile(
    rf"^({_NUM})\s*-?\s*({'|'.join(_COUNT_SUFFIX)})(?:\s*\+\s*.+)?$",
    re.IGNORECASE,
)
_FT_IN_RE = re.compile(rf"^({_NUM})\s*'\s*({_NUM})?\s*(?:\"|'')?$")
_NUM_ONLY_RE = re.compile(rf"^{_NUM}$")

# Vieneto normalizavimas -> kanoninis tokenas
_UNIT_NORM = {
    "lbs": "lb", "yds": "yd", "'": "ft", "''": "in", '"': "in",
    "gr": "g", "gr.": "g", "gram": "g", "grams": "g",
    "mtr": "m", "meter": "m", "metres": "m",
    "inch": "in", "inches": "in",
    "pc": "", "pcs": "",
}

# Vienetų grupės -> koeficientas į bazinį vienetą (mm / g / ml / count)
_UNIT_GROUP = [
    {"mm": Decimal("1"), "cm": Decimal("10"), "m": Decimal("1000"),
     "in": Decimal("25.4"), "ft": Decimal("304.8"), "yd": Decimal("914.4")},
    {"g": Decimal("1"), "kg": Decimal("1000"),
     "oz": Decimal("28.3495"), "lb": Decimal("453.59237")},
    {"ml": Decimal("1"), "l": Decimal("1000")},
    {"": Decimal("1")},
]

# Apvalinimo ženklai pagal kanoninį vienetą
_ROUNDING = {
    "mm": 3, "cm": 1, "m": 2, "ft": 2, "in": 2, "yd": 2,
    "g": 1, "kg": 2, "lb": 2, "oz": 1, "ml": 0, "l": 2, "": 2,
}

# Sanity ribos (kanoniniame vienete) – peržengus report'inama,
# bet reikšmė vis tiek eksportuojama.
_SUSPICIOUS_LIMITS = {
    "weight": Decimal("50000"),          # g
    "line_diameter": Decimal("3"),       # mm
    "rod_length_m": Decimal("20"),       # m
    "casting_weight": Decimal("1000"),   # g (num_max)
    "line_strength": Decimal("200"),     # kg
    "unit_length": Decimal("500"),       # cm
    "unit_width": Decimal("500"),
    "unit_height": Decimal("500"),
}


@dataclass
class ParsedValue:
    num: Decimal | None
    num_max: Decimal | None
    unit: str
    display: str
    code: str
    suspicious: bool = False


def _to_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(str(text).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _factor(unit: str) -> Decimal | None:
    for group in _UNIT_GROUP:
        if unit in group:
            return group[unit]
    return None


def _convert(value: Decimal, from_unit: str, to_unit: str) -> Decimal | None:
    if from_unit == to_unit or not to_unit:
        return value
    for group in _UNIT_GROUP:
        if from_unit in group and to_unit in group:
            return value * group[from_unit] / group[to_unit]
    return None


def _unit_group_of(unit: str) -> int | None:
    for i, group in enumerate(_UNIT_GROUP):
        if unit in group:
            return i
    return None


def _round(value: Decimal, unit: str, source: Decimal | None = None) -> Decimal:
    """Apvalina konversijos liekanas (9.0718474 -> 9.07), bet niekada
    nenumeta šaltinyje buvusių skaitmenų: 0.15 g lieka 0.15, ne 0.2
    (`source` – nekonvertuota reikšmė; be konversijos source == value)."""
    places = _ROUNDING.get(unit, 2)
    if source is not None:
        places = max(places, -source.normalize().as_tuple().exponent)
    quantum = Decimal(1) if places == 0 else Decimal(1).scaleb(-places)
    try:
        return value.quantize(quantum).normalize()
    except InvalidOperation:
        return value.normalize()


def _fmt_num(value: Decimal, *, comma: bool = True) -> str:
    """'2.70' -> '2,7' (comma=True) arba '2.7' – be trailing zeros."""
    s = format(value.normalize(), "f")
    return s.replace(".", ",") if comma else s


def _fmt_code_num(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _axis_key(axis: str) -> str:
    return str(axis or "").strip().lower().replace("-", "_")


def _check_suspicious(
    axis: str, unit: str, num: Decimal, num_max: Decimal | None
) -> bool:
    limit = _SUSPICIOUS_LIMITS.get(_axis_key(axis))
    if limit is None:
        # Bendra g riba nežinomiems svorio tipo atributams (pvz. 227000 g).
        if unit == "g":
            limit = Decimal("50000")
        else:
            return False
    worst = num_max if num_max is not None else num
    return worst > limit


def _text_value(axis: str, raw) -> tuple[str, str]:
    """enum/text/bool kelias – buvusi normalize_option_value logika."""
    text = re.sub(r"\s+", " ", str(strip_axis_prefix(raw))).strip()
    if not is_meaningful_value(text):
        return "", ""
    norm = text.lower().replace(" ", "")
    flat = norm.replace("-", "").replace("_", "")
    alias = {
        "xsmall": "xs", "xtrasmall": "xs", "extrasmall": "xs",
        "xlarge": "xl", "xtralarge": "xl", "extralarge": "xl",
        "xxlarge": "2xl", "xxxlarge": "3xl",
        "onesize": "one-size", "onesizefitsall": "one-size",
        "free": "one-size", "freesize": "one-size", "uni": "one-size",
        "small": "s", "medium": "m", "large": "l",
        "std": "standard",
    }
    if flat in alias:
        code = alias[flat]
        return code, "One Size" if code == "one-size" else code.upper()
    m = re.fullmatch(r"(x+|\d+x)l", norm)
    if m:
        head = m.group(1)
        n = len(head) if head.startswith("x") else int(head[:-1])
        code = "xl" if n == 1 else f"{n}xl"
        return code, code.upper()
    if norm in ("xxs", "xs", "s", "m", "l"):
        return ("2xs" if norm == "xxs" else norm), norm.upper().replace("XXS", "2XS")
    # skaičius + vienetas: '0,08MM' -> '0-08mm' / '0,08 mm'
    mu = re.fullmatch(rf"({_NUM})\s*({_UNIT_RE})", norm, re.IGNORECASE)
    if mu:
        num, unit = mu.group(1), _UNIT_NORM.get(mu.group(2).lower(), mu.group(2).lower())
        return value_code(f"{num}{unit}"), f"{num.replace('.', ',')} {unit}"
    # Plikas skaičius ašyje su žinomu vienetu -> label su vienetu
    meta = ATTRIBUTE_META.get(_axis_key(axis))
    axis_unit = (meta or {}).get("unit") or ""
    if axis_unit and _NUM_ONLY_RE.fullmatch(norm):
        return value_code(f"{norm}{axis_unit}"), f"{norm.replace('.', ',')} {axis_unit}"
    return value_code(text), text


def parse_value(
    text, datatype: str, unit: str, axis: str = "", supplier: str = ""
) -> ParsedValue:
    """tekstas -> ParsedValue. `unit` = atributo kanoninis vienetas."""
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    canonical_unit = (unit or "").strip().lower()
    if datatype not in ("number", "range"):
        code, display = _text_value(axis, raw)
        return ParsedValue(
            num=None, num_max=None, unit=canonical_unit,
            display=display, code=code,
        )
    if not is_meaningful_value(raw):
        return ParsedValue(None, None, canonical_unit, "", "")

    num = num_max = None
    detected_unit = ""
    orig_display = ""

    def _norm_u(tok: str) -> str:
        return _UNIT_NORM.get((tok or "").lower(), (tok or "").lower())

    def _num_unit(text: str):
        """'11cm'/'4.25"'/'224 gr.' -> (Decimal, unit) arba None."""
        mm = _SINGLE_RE.match(text.strip())
        if not mm:
            return None
        n = _to_decimal(mm.group(1))
        u = _norm_u(mm.group(2) or "")
        if n is None or not u:
            return None
        return n, u

    m = _FT_IN_RE.match(raw)
    if m:
        feet = _to_decimal(m.group(1))
        inches = _to_decimal(m.group(2)) or Decimal(0)
        num = feet + inches / Decimal(12)
        detected_unit = "ft"
        orig_display = f"{_fmt_num(num)} ft"
    else:
        m = _RANGE_RE.match(raw)
        if m:
            u_lo = _norm_u(m.group(2) or "")
            u_hi = _norm_u(m.group(4) or "")
            if u_lo and u_hi and u_lo != u_hi:
                m = None  # '10g-40cm' – klaidinga forma
            else:
                num = _to_decimal(m.group(1))
                num_max = _to_decimal(m.group(3))
                detected_unit = u_lo or u_hi
        if m is None:
            m = _SINGLE_RE.match(raw)
            if m:
                num = _to_decimal(m.group(1))
                detected_unit = _norm_u(m.group(2) or "")
        if num is None and canonical_unit == "":
            # Bematis atributas – 'vieneto' sufiksas yra skaičiaus žymuo:
            # '2pc', '3 pcs', '2 sk', '3-tlg', '2 sections', '4pc+tips'.
            mc = _COUNT_RE.match(raw)
            if mc:
                num = _to_decimal(mc.group(1))
                detected_unit = ""
        if num is None and "/" in raw:
            # Dual-unit '11cm/4.25"', '8oz / 224 gr.', '2.70m / 9'':
            # abi pusės turi turėti vienetą iš TO PATIES vieneto grupės;
            # imame pusę su kanoniniu vienetu, kitaip pirmą konvertuojamą.
            halves = raw.split("/")
            if len(halves) == 2:
                a = _num_unit(halves[0])
                b = _num_unit(halves[1])
                if a and b:
                    ga = _unit_group_of(a[1])
                    gb = _unit_group_of(b[1])
                    if ga is not None and ga == gb:
                        picked = None
                        if b[1] == canonical_unit:
                            picked = b
                        elif a[1] == canonical_unit:
                            picked = a
                        elif _convert(a[0], a[1], canonical_unit) is not None:
                            picked = a
                        elif _convert(b[0], b[1], canonical_unit) is not None:
                            picked = b
                        if picked is not None:
                            num, detected_unit = picked
                            # be '(originalas)' sufikso – orig_display lieka ""
        if m and detected_unit and num is not None:
            orig = num if num_max is None else None
            if orig is not None:
                orig_display = f"{_fmt_num(orig)} {detected_unit}"
            else:
                orig_display = f"{raw}"

    if num is None:
        report(
            supplier=supplier or None, code=_axis_key(axis), kind="unparsed_value",
            value=raw,
        )
        return ParsedValue(
            num=None, num_max=None, unit=canonical_unit,
            display=raw, code=value_code(raw),
        )

    converted = _convert(num, detected_unit, canonical_unit) if detected_unit else num
    converted_max = (
        _convert(num_max, detected_unit, canonical_unit)
        if (num_max is not None and detected_unit)
        else num_max
    )
    if converted is None:
        # neatpažintas vienetas – paliekam tekstą
        report(
            supplier=supplier or None, code=_axis_key(axis), kind="unparsed_value",
            value=raw,
        )
        return ParsedValue(
            num=None, num_max=None, unit=canonical_unit,
            display=raw, code=value_code(raw),
        )
    unconverted = not detected_unit or detected_unit == canonical_unit
    num = _round(converted, canonical_unit, num if unconverted else None)
    if converted_max is not None:
        num_max = _round(
            converted_max, canonical_unit, num_max if unconverted else None
        )
        if num_max < num:
            num, num_max = num_max, num

    suspicious = _check_suspicious(axis, unit, num, num_max)
    if suspicious:
        report(
            supplier=supplier or None, code=_axis_key(axis), kind="suspicious_value",
            value=raw,
        )

    if num_max is not None:
        num_part = f"{_fmt_num(num)}-{_fmt_num(num_max)}"
    else:
        num_part = _fmt_num(num)
    display = f"{num_part} {canonical_unit}" if canonical_unit else num_part
    if detected_unit and detected_unit != canonical_unit and orig_display:
        display = f"{display} ({orig_display})"

    code_num = (
        f"{_fmt_code_num(num)}-{_fmt_code_num(num_max)}"
        if num_max is not None
        else _fmt_code_num(num)
    )
    code = value_code(f"{code_num}-{canonical_unit}" if canonical_unit else code_num)
    return ParsedValue(
        num=num, num_max=num_max, unit=canonical_unit,
        display=display, code=code, suspicious=suspicious,
    )
