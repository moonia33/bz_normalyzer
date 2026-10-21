"""Kanoninių prekės atributų registras.

Spec stulpelių label'iai (EN/PL/DE ir kt.) -> kanoninis kodas + tipas:
  axis     – variantų pasirinkimo ašis (options)
  feature  – techninė charakteristika (features)
  shipping – pakuotės matmuo (Height/Width/Length/Weight)
  ignore   – nenaudojamas (duplikuotas tech aprašyme, logistikos vidiniai,
             variklio/baterijų laukai)

Kodai stabilūs (shop Feature/Option slug'ai). Naujas tiekėjas -> pridedami
label'iai prie esamų kodų, o ne nauji kodai.
"""

from __future__ import annotations

import re

# (code, kind, labelai, datatype, unit). Label'iai case-insensitive
# match'inami strip'inti. datatype ∈ {number, range, enum, text, bool};
# unit – kanoninis vienetas ("" kai bematis). Kodai snake_case; eksporto
# riba (_code()) verčia į '-' formą.
_CANONICAL: list[tuple[str, str, list[str], str, str]] = [
    # ---- AŠYS (options) – eilės tvarka = prioritetas ribojant iki 3 ----
    ("model", "axis", ["Model", "Modell"], "text", ""),
    ("color", "axis", ["Color", "Colour", "Farbe", "Kolor"], "text", ""),
    ("rod_length_m", "axis", ["Rod Length m", "Rutenlänge m", "Długość wędki m"], "number", "m"),
    ("rod_length_ft", "axis", ["Rod Length ft", "Rutenlänge ft", "Długość wędki ft"], "number", "ft"),
    ("testcurve", "axis", ["Testcurve", "Test Curve", "Testkurve", "Krzywa ugięcia"], "number", "lb"),
    ("sections", "axis", ["Sections", "Teile", "Sekcje"], "number", ""),
    ("casting_weight", "axis", ["Casting Weight gram", "Casting Weight", "Wurfgewicht g", "Ciężar wyrzutowy g"], "range", "g"),
    ("tip_diameter", "axis", ["Tip Ø", "Ø Spitze", "Ø szczytówki", "Tip Diameter"], "number", "mm"),
    # 'rozmer'/'dydis'/'wymiar' – generic dydžio laukai (MOSS <ROZMER>);
    # kompozitinės reikšmės -> apps.catalog.profiles.split_composite.
    ("size", "axis", ["Size", "Größe", "Rozmiar", "Rozmer", "Dydis", "Wymiar"], "text", ""),
    ("hook_size", "axis", ["Hook size", "Hook Size", "Hakengröße", "Rozmiar haka"], "text", ""),
    ("pattern", "axis", ["Decor Pattern", "Dekor", "Dekoracja", "Pattern"], "text", ""),
    ("lure_size", "axis", ["Lure Size", "Ködergröße", "Rozmiar przynęty"], "number", "cm"),
    ("weight", "axis", ["Weight gram", "Gewicht g", "Waga g"], "number", "g"),
    ("volume", "axis", ["Liquid Volume", "Flüssigkeitsvolumen", "Objętość płynu"], "number", "ml"),
    ("line_diameter", "axis", ["Line Ø", "Ø Schnur", "Ø żyłki", "Line Diameter"], "number", "mm"),
    ("line_length", "axis", ["Line Length m", "Schnurlänge m", "Długość żyłki m"], "number", "m"),
    ("line_strength", "axis", ["Line Strength", "Strength", "Breaking Strain",
                               "Line Test kg", "Line Test lbs", "Line Rating lbs",
                               "Schnurstärke kg", "Schnurstärke lbs",
                               "Wytrzymałość żyłki kg", "Wytrzymałość żyłki lbs"], "number", "kg"),
    ("action", "axis", ["Action", "Aktion", "Akcja"], "text", ""),
    ("rod_type", "axis", ["Rod Type"], "text", ""),
    ("egi", "axis", ["Egi"], "text", ""),
    ("reel_size", "axis", ["Reel Size"], "text", ""),
    # Gaminio matmenys (ne pakuotės) – kai varijuoja grupėje, tai tikros
    # ašys (graibšto/matelio ilgis, krepšio plotis ir t.t.). Žemiausias
    # prioritetas, kad nevaržytų model/color/size.
    ("unit_length", "axis", ["Unit Length", "Einheitslänge", "Długość jednostki"], "number", "cm"),
    ("unit_width", "axis", ["Unit Width", "Einheitsbreite", "Szerokość jednostki"], "number", "cm"),
    ("unit_height", "axis", ["Unit Height", "Einheitshöhe", "Wysokość jednostki"], "number", "cm"),
    # ---- FEATURES ----
    ("transport_length", "feature", ["Transport Length cm", "Transportlänge cm", "Długość transportowa cm"], "number", "cm"),
    ("guides_qty", "feature", ["Guides QTY", "Guides Quantity", "Ringanzahl", "Liczba przelotek"], "number", ""),
    ("guides_size", "feature", ["Guides Size", "Ringgröße", "Rozmiar przelotek"], "text", ""),
    ("guides_type", "feature", ["GuidesType", "Guides Type", "Ringtyp", "Rodzaj przelotek"], "text", ""),
    ("rod_blank_material", "feature", ["Rod Blank Material", "Blankmaterial", "Materiał blanku"], "text", ""),
    ("ball_bearings", "feature", ["Ball Bearings", "Kugellager", "Łożyska kulkowe"], "text", ""),
    ("mono_capacity", "feature", ["Mono Line Capacity m/mm", "Monofil Kapazität m/mm", "Pojemność żyłki nylon m/mm", "Mono Line Capacity"], "text", ""),
    ("braid_capacity", "feature", ["Braid Line Capacity m/mm", "Geflecht Kapazität m/mm", "Pojemność plecionki m/mm", "Braid Line Capacity"], "text", ""),
    ("gear_ratio", "feature", ["Gear Ratio", "Übersetzung", "Przełożenie"], "text", ""),
    ("battery_included", "feature", ["Battery included", "Batterie enthalten", "Bateria w zestawie"], "bool", ""),
    ("diving_depth", "feature", ["Diving Depth m", "Tauchtiefe m", "Głębokość pracy m"], "number", "m"),
    ("compartments", "feature", ["Compartments", "Fächer", "Przegrody"], "number", ""),
    ("feedertips", "feature", ["Feedertips", "Feedertip", "Feederspitzen", "Szczytówki"], "text", ""),
    ("diameter", "feature", ["Ø"], "number", "mm"),
    ("hoop_diameter", "feature", ["Hoop Diameter"], "number", "cm"),
    ("material_fabric", "feature", ["Material/Fabric Information", "Material / Fabric Information"], "text", ""),
    ("flavour", "axis", ["Flavour", "Flavor", "Aroma", "Aromat"], "text", ""),
    # ---- SHIPPING (Retail* -> Height/Width/Length/Weight) ----
    ("height", "shipping", ["Retail Height cm", "Handelshöhe cm", "Wysokość detaliczna cm"], "number", "cm"),
    ("width", "shipping", ["Retail Width cm", "Handelsbreite cm", "Szerokość detaliczna cm"], "number", "cm"),
    ("length", "shipping", ["Retail Depth cm", "Handelstiefe cm", "Głębokość detaliczna cm"], "number", "cm"),
    ("weight_shipping", "shipping", ["Retail Weight gram", "Handelsgewicht g", "Waga detaliczna g"], "number", "g"),
    # ---- IGNORE ----
    ("_transport_height", "ignore", ["Transport Height", "Transporthöhe", "Wysokość transportu"]),
    ("_transport_width", "ignore", ["Transport Width", "Transportbreite", "Szerokość transportu"]),
    ("_transport_length_raw", "ignore", ["Transport Length", "Transportlänge", "Długość transportu"]),
    ("_reel_seat", "ignore", ["Reel Seat", "Rollenhalter", "Uchwyt kołowrotka"]),
    ("_drag_kg", "ignore", ["Drag Force kg", "Bremskraft kg", "Siła hamulca kg"]),
    ("_drag_lbs", "ignore", ["Drag Force lbs", "Bremskraft lbs", "Siła hamulca lbs"]),
    ("_retrieve", "ignore", ["Retrieve cm", "Schnureinzug cm", "Ściąganie cm"]),
    ("_reel_frame", "ignore", ["Reel Frame Material", "Rollenkörpermaterial", "Materiał korpusu kołowrotka"]),
    ("_line_length_yds", "ignore", ["Line Length yds", "Schnurlänge yds", "Długość żyłki yds"]),
    ("_battery_required", "ignore", ["Battery Required", "Batterie erforderlich", "Wymagana bateria"]),
    ("_battery_type_1", "ignore", ["Battery Type 1", "Batterietyp 1", "Rodzaj baterii 1"]),
    ("_battery_type_2", "ignore", ["Battery Type 2", "Batterietyp 2", "Rodzaj baterii 2"]),
    ("_battery_count_1", "ignore", ["Numbers of Batteries 1", "Anzahl Batterien 1", "Ilość baterii 1"]),
    ("_battery_count_2", "ignore", ["Numbers of Batteries 2", "Anzahl Batterien 2", "Ilość baterii 2"]),
    ("_battery_weight_1", "ignore", ["Battery Weight 1", "Batteriegewicht 1", "Waga baterii 1"]),
    ("_battery_weight_2", "ignore", ["Battery Weight 2", "Batteriegewicht 2", "Waga baterii 2"]),
    ("_battery_total", "ignore", ["Battery total weight", "Gesamtgewicht Batterien", "Całkowita waga baterii"]),
    ("_motor_boat_weight", "ignore", ["Motor MaxBoat Weight", "Max. Bootsgewicht (Motor)", "Maks. waga łodzi (silnik)"]),
    ("_motor_cable", "ignore", ["Motor Length Battery Cable", "Motor Batteriekabellänge", "Długość kabla baterii silnika"]),
    ("_motor_shaft", "ignore", ["Motor Shaft Length", "Motorschaftlänge", "Długość wału silnika"]),
    ("_motor_volt", "ignore", ["Motor Volt", "Napięcie silnika"]),
    ("_motor_type", "ignore", ["Motor Type", "Motortyp", "Rodzaj silnika"]),
    ("_motor_speeds", "ignore", ["Motor Speeds", "Motorgeschwindigkeiten", "Prędkości silnika"]),
    ("_motor_rc", "ignore", ["Motor RC Frequency", "Motor RC Frequenz", "Częstotliwość RC silnika"]),
    ("_motor_current", "ignore", ["Motor Max Curren A", "Motor Max Current A", "Max. Strom A", "Maks. prąd A"]),
    ("_motor_box", "ignore", ["Motor Battery Box Ah", "Motor Batteriebox Ah", "Pojemność skrzynki baterii Ah"]),
    ("_motor_power", "ignore", ["Motor Power Input W", "Leistungsaufnahme W", "Moc pobierana W"]),
]

# Meta stulpeliai – niekur nesiklasifikuoja (identifikatoriai, ne atributai)
META_LABELS = {
    "art-no.", "art-no", "name", "brand", "product group",
    "nazwa", "nr art.", "marka", "grupa produktów",
    "art.-nr.", "produktgruppe", "marke",
}

# Indeksai label -> (code, kind) ir code -> meta
LABEL_MAP: dict[str, tuple[str, str]] = {}
ATTRIBUTE_META: dict[str, dict] = {}
_AXIS_ORDER: list[str] = []

for _entry in _CANONICAL:
    code, kind, labels = _entry[0], _entry[1], _entry[2]
    datatype, unit = (_entry[3], _entry[4]) if len(_entry) > 3 else ("text", "")
    ATTRIBUTE_META[code] = {
        "kind": kind,
        "label": labels[0],
        "datatype": datatype,
        "unit": unit,
    }
    if kind == "axis":
        _AXIS_ORDER.append(code)
    for label in labels:
        LABEL_MAP[label.strip().lower()] = (code, kind)

# Ašies prioritetas mažesnis = svarbesnė (ribojant iki 3 options)
AXIS_PRIORITY: dict[str, int] = {code: i for i, code in enumerate(_AXIS_ORDER)}

MAX_OPTIONS = 3

# Legacy tiekėjų kodai -> kanoninis (raktai lyginami po
# .lower().replace("-", "_"); reikšmių vienetų konversija – values.py).
CODE_ALIASES: dict[str, str] = {
    "strength": "line_strength",
    "strength_lb": "line_strength",
    "line_test_kg": "line_strength",
    "line_test_lbs": "line_strength",
    "line_rating_lbs": "line_strength",
    "length_imperial": "rod_length_ft",
    "section": "sections",
}

# (code, category_regex, canonical) – regex taikomas lowercase
# kontekstui: kategorijos (+parent'ų) names + category_raw +
# sub_category_raw. Pirmasis match'as laimi.
_RODS_CTX = r"spinin|\brods?\b|wędk|wedk|meškerykoč|meskerykoc|meškere|meskere|karpin|feeder|\bmatch\b|\bpoles?\b|\bfly\b|bolonij|\bsurf"
_LINES_CTX = r"\bvalai|\bvalas|\blines?\b|braid|\bmono\b|fluoro|żyłk|zylk|plecion|pinti|monofil"
CONTEXT_RULES: list[tuple[str, str, str]] = [
    ("length", _RODS_CTX, "rod_length_m"),
    ("length", _LINES_CTX, "line_length"),
    ("diameter", _LINES_CTX, "line_diameter"),
    ("diameter", r"graibšt|graibst|\bnets?\b|podbier|landing", "hoop_diameter"),
]

# Kodai, kurių galutinė tapatybė priklauso nuo konteksto.
_CONTEXTUAL_CODES = {code for code, _rx, _c in CONTEXT_RULES}

# 'variantas' nėra atributas – liekana eina į variant_label.
VARIANT_LABEL_CODE = "variantas"


def _norm_code(code: str) -> str:
    return str(code or "").strip().lower().replace("-", "_").replace(" ", "_")


def resolve_code(code: str, context: str = "") -> str | None:
    """Tiekėjo atributo kodas -> kanoninis registro kodas arba None.

    Tvarka: 'variantas' -> None (atskiras kelias), alias'ai, kontekstinės
    taisyklės, registras. None = neeksportuojamas (report'uojamas).
    """
    c = _norm_code(code)
    if not c or c == VARIANT_LABEL_CODE:
        return None
    c = CODE_ALIASES.get(c, c)
    if c in _CONTEXTUAL_CODES:
        for rule_code, pattern, canonical in CONTEXT_RULES:
            if c == rule_code and re.search(pattern, (context or "").lower()):
                return canonical
        # 'diameter' be konteksto lieka pats; 'length' – nemapintas.
        return "diameter" if c == "diameter" else None
    return c if c in ATTRIBUTE_META else None


def classify_label(label: str) -> tuple[str, str]:
    """label -> (code, kind). Nežinomas -> (slug, 'feature')."""
    key = (label or "").strip().lower()
    if not key or key in META_LABELS:
        return "", "ignore"
    if key in LABEL_MAP:
        return LABEL_MAP[key]
    from django.utils.text import slugify

    return slugify(label)[:60] or "misc", "feature"


def value_code(value: str) -> str:
    """Option/feature reikšmės kodas – stabilus slug.

    'Black/Grey' -> 'black-grey', '5.1:1' -> '5-1-1', '0,18mm' -> '0-18mm'.
    """
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value).strip())
    text = text.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:80] or "value"


def strip_axis_prefix(value: str) -> str:
    """'Color:Dark/Grey' -> 'Dark/Grey' (dubliuotas ašies pavadinimas value)."""
    text = str(value).strip()
    if ":" in text:
        head, _, tail = text.partition(":")
        if head.strip().lower() in LABEL_MAP or head.strip().lower() in META_LABELS:
            return tail.strip()
    return text


# Reikšmės-be-informacijos: netampa nei option, nei feature reikšmėmis.
EMPTY_ATTR_VALUES = {
    "", "none", "n/a", "na", "null", "-", "--", "—", "–", "no",
    "0", "0.0", "0,0", "0.00", "0,00",
}


def is_meaningful_value(value) -> bool:
    """Ar atributo reikšmė informatyvi (ne tuščia/'none'/'0' ir t.t.)."""
    if value is None:
        return False
    text = str(value).strip().lower()
    return bool(text) and text not in EMPTY_ATTR_VALUES


def value_in_name(axis: str, raw_value, name: str) -> bool:
    """Ar ašies reikšmė atsiranda varianto pavadinime.

    '1.75m'/'1,75 m' atitinka 'Holdall 1.75m' (sulygiuota ,/. ir tarpai).
    Grynoms skaitinėms reikšmėms reikalaujama skaitmens ribų abiejose
    pusėse, kad '200' neatitiktų '1200'.
    """
    import re

    flat_value = re.sub(r"[^a-z0-9.]+", "", str(raw_value or "").lower().replace(",", "."))
    if len(flat_value) < 2:
        return False
    flat_name = re.sub(r"[^a-z0-9.]+", "", str(name or "").lower().replace(",", "."))
    if flat_value not in flat_name:
        return False
    for match in re.finditer(re.escape(flat_value), flat_name):
        before = flat_name[match.start() - 1] if match.start() else ""
        after = flat_name[match.end()] if match.end() < len(flat_name) else ""
        if flat_value[0].isdigit() and before.isdigit():
            continue
        if after.isdigit():
            continue
        return True
    return False


# Vienetai, atpažįstami "skaičius+vienetas" reikšmėse (option value normalizacijai)
_UNITS = (
    "mm", "cm", "m", "g", "kg", "lbs", "lb", "oz", "ft", "in",
    "yd", "yds", "ml", "l", "ah", "w", "v", "a",
)
_UNIT_VALUE_RE = None  # kompiliuojama tingiai normalize_option_value


def normalize_option_value(axis: str, raw) -> tuple[str, str]:
    """(canonical_code, human_label) – plonas wrapper'is virš parse_value.

    - Dydžių aliasai: 'XXL'/'xxl'/'2XL'/'Xxl'/'XX-Large' -> '2xl' ('2XL')
    - Vienetai: '0,08MM'/'0.08 mm' -> kodas '0-08-mm', label '0,08 mm'
    - Case: 'White'/'white' -> tas pats kodas 'white'
    """
    from apps.catalog.values import parse_value

    meta = ATTRIBUTE_META.get(_norm_code(axis)) or {}
    parsed = parse_value(
        raw,
        meta.get("datatype", "text"),
        meta.get("unit", ""),
        axis=_norm_code(axis),
    )
    return parsed.code, parsed.display
