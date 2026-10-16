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

# (code, kind, labelai). Label'iai case-insensitive match'inami strip'inti.
_CANONICAL: list[tuple[str, str, list[str]]] = [
    # ---- AŠYS (options) – eilės tvarka = prioritetas ribojant iki 3 ----
    ("model", "axis", ["Model", "Modell"]),
    ("color", "axis", ["Color", "Colour", "Farbe", "Kolor"]),
    ("rod_length_m", "axis", ["Rod Length m", "Rutenlänge m", "Długość wędki m"]),
    ("rod_length_ft", "axis", ["Rod Length ft", "Rutenlänge ft", "Długość wędki ft"]),
    ("testcurve", "axis", ["Testcurve", "Test Curve", "Testkurve", "Krzywa ugięcia"]),
    ("sections", "axis", ["Sections", "Teile", "Sekcje"]),
    ("casting_weight", "axis", ["Casting Weight gram", "Casting Weight", "Wurfgewicht g", "Ciężar wyrzutowy g"]),
    ("tip_diameter", "axis", ["Tip Ø", "Ø Spitze", "Ø szczytówki", "Tip Diameter"]),
    ("size", "axis", ["Size", "Größe", "Rozmiar"]),
    ("hook_size", "axis", ["Hook size", "Hook Size", "Hakengröße", "Rozmiar haka"]),
    ("pattern", "axis", ["Decor Pattern", "Dekor", "Dekoracja", "Pattern"]),
    ("lure_size", "axis", ["Lure Size", "Ködergröße", "Rozmiar przynęty"]),
    ("weight", "axis", ["Weight gram", "Gewicht g", "Waga g"]),
    ("volume", "axis", ["Liquid Volume", "Flüssigkeitsvolumen", "Objętość płynu"]),
    ("line_diameter", "axis", ["Line Ø", "Ø Schnur", "Ø żyłki", "Line Diameter"]),
    ("line_length", "axis", ["Line Length m", "Schnurlänge m", "Długość żyłki m"]),
    # Gaminio matmenys (ne pakuotės) – kai varijuoja grupėje, tai tikros
    # ašys (graibšto/matelio ilgis, krepšio plotis ir t.t.). Žemiausias
    # prioritetas, kad nevaržytų model/color/size.
    ("unit_length", "axis", ["Unit Length", "Einheitslänge", "Długość jednostki"]),
    ("unit_width", "axis", ["Unit Width", "Einheitsbreite", "Szerokość jednostki"]),
    ("unit_height", "axis", ["Unit Height", "Einheitshöhe", "Wysokość jednostki"]),
    # ---- FEATURES ----
    ("transport_length", "feature", ["Transport Length cm", "Transportlänge cm", "Długość transportowa cm"]),
    ("guides_qty", "feature", ["Guides QTY", "Guides Quantity", "Ringanzahl", "Liczba przelotek"]),
    ("guides_size", "feature", ["Guides Size", "Ringgröße", "Rozmiar przelotek"]),
    ("guides_type", "feature", ["GuidesType", "Guides Type", "Ringtyp", "Rodzaj przelotek"]),
    ("rod_blank_material", "feature", ["Rod Blank Material", "Blankmaterial", "Materiał blanku"]),
    ("ball_bearings", "feature", ["Ball Bearings", "Kugellager", "Łożyska kulkowe"]),
    ("mono_capacity", "feature", ["Mono Line Capacity m/mm", "Monofil Kapazität m/mm", "Pojemność żyłki nylon m/mm", "Mono Line Capacity"]),
    ("braid_capacity", "feature", ["Braid Line Capacity m/mm", "Geflecht Kapazität m/mm", "Pojemność plecionki m/mm", "Braid Line Capacity"]),
    ("gear_ratio", "feature", ["Gear Ratio", "Übersetzung", "Przełożenie"]),
    ("battery_included", "feature", ["Battery included", "Batterie enthalten", "Bateria w zestawie"]),
    ("diving_depth", "feature", ["Diving Depth m", "Tauchtiefe m", "Głębokość pracy m"]),
    ("line_test_lbs", "feature", ["Line Test lbs", "Schnurklasse lbs", "Klasa żyłki lbs", "Schnurstärke lbs", "Wytrzymałość żyłki lbs"]),
    ("line_test_kg", "feature", ["Line Test kg", "Schnurstärke kg", "Wytrzymałość żyłki kg"]),
    ("compartments", "feature", ["Compartments", "Fächer", "Przegrody"]),
    ("feedertips", "feature", ["Feedertips", "Feedertip", "Feederspitzen", "Szczytówki"]),
    ("diameter", "feature", ["Ø"]),
    ("material_fabric", "feature", ["Material/Fabric Information", "Material / Fabric Information"]),
    # ---- SHIPPING (Retail* -> Height/Width/Length/Weight) ----
    ("height", "shipping", ["Retail Height cm", "Handelshöhe cm", "Wysokość detaliczna cm"]),
    ("width", "shipping", ["Retail Width cm", "Handelsbreite cm", "Szerokość detaliczna cm"]),
    ("length", "shipping", ["Retail Depth cm", "Handelstiefe cm", "Głębokość detaliczna cm"]),
    ("weight_shipping", "shipping", ["Retail Weight gram", "Handelsgewicht g", "Waga detaliczna g"]),
    # ---- IGNORE ----
    ("_transport_height", "ignore", ["Transport Height", "Transporthöhe", "Wysokość transportu"]),
    ("_transport_width", "ignore", ["Transport Width", "Transportbreite", "Szerokość transportu"]),
    ("_transport_length_raw", "ignore", ["Transport Length", "Transportlänge", "Długość transportu"]),
    ("_action", "ignore", ["Action", "Aktion", "Akcja"]),
    ("_reel_seat", "ignore", ["Reel Seat", "Rollenhalter", "Uchwyt kołowrotka"]),
    ("_drag_kg", "ignore", ["Drag Force kg", "Bremskraft kg", "Siła hamulca kg"]),
    ("_drag_lbs", "ignore", ["Drag Force lbs", "Bremskraft lbs", "Siła hamulca lbs"]),
    ("_retrieve", "ignore", ["Retrieve cm", "Schnureinzug cm", "Ściąganie cm"]),
    ("_reel_frame", "ignore", ["Reel Frame Material", "Rollenkörpermaterial", "Materiał korpusu kołowrotka"]),
    ("_line_length_yds", "ignore", ["Line Length yds", "Schnurlänge yds", "Długość żyłki yds"]),
    ("_flavour", "ignore", ["Flavour", "Flavor", "Aroma", "Aromat"]),
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

for code, kind, labels in _CANONICAL:
    ATTRIBUTE_META[code] = {"kind": kind, "label": labels[0]}
    if kind == "axis":
        _AXIS_ORDER.append(code)
    for label in labels:
        LABEL_MAP[label.strip().lower()] = (code, kind)

# Ašies prioritetas mažesnis = svarbesnė (ribojant iki 3 options)
AXIS_PRIORITY: dict[str, int] = {code: i for i, code in enumerate(_AXIS_ORDER)}

MAX_OPTIONS = 3


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


# Vienetai, atpažįstami "skaičius+vienetas" reikšmėse (option value normalizacijai)
_UNITS = (
    "mm", "cm", "m", "g", "kg", "lbs", "lb", "oz", "ft", "in",
    "yd", "yds", "ml", "l", "ah", "w", "v", "a",
)
_UNIT_VALUE_RE = None  # kompiliuojama tingiai normalize_option_value


def normalize_option_value(axis: str, raw) -> tuple[str, str]:
    """(canonical_code, human_label) – filtrų reikšmių unifikavimas.

    - Dydžių aliasai: 'XXL'/'xxl'/'2XL'/'Xxl'/'XX-Large' -> '2xl' ('2XL')
    - Vienetai: '0,08MM'/'0.08 mm' -> kodas '0-08mm', label '0,08 mm'
    - Case: 'White'/'white' -> tas pats kodas 'white'
    """
    global _UNIT_VALUE_RE
    import re

    if _UNIT_VALUE_RE is None:
        _UNIT_VALUE_RE = re.compile(
            rf"^(\d+(?:[.,]\d+)?)\s*({'|'.join(_UNITS)})$", re.IGNORECASE
        )
    text = re.sub(r"\s+", " ", str(strip_axis_prefix(raw))).strip()
    if not text:
        return "", ""
    norm = text.lower().replace(" ", "")
    # Dydžių aliasai – flat raktas be tarpų/brūkšnelių ('X-Large'->'xl')
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
    # skaičius + vienetas: '0,08MM' -> '0-08mm' / '0,08 mm'; '200 M' -> '200m' / '200 m'
    mu = _UNIT_VALUE_RE.match(norm)
    if mu:
        num, unit = mu.group(1), mu.group(2).lower()
        return value_code(f"{num}{unit}"), f"{num.replace('.', ',')} {unit}"
    # Plikas skaičius ašyje su žinomu vienetu -> label su vienetu
    axis_unit = {
        "line_diameter": "mm", "tip_diameter": "mm", "diameter": "mm",
        "line_length": "m", "rod_length_m": "m", "rod_length_ft": "ft",
        "weight": "g", "casting_weight": "g",
        "unit_length": "cm", "unit_width": "cm", "unit_height": "cm",
        "transport_length": "cm", "diving_depth": "m",
        "lure_size": "cm", "volume": "ml",
    }.get(axis)
    if axis_unit and re.fullmatch(r"\d+(?:[.,]\d+)?", norm):
        return value_code(f"{norm}{axis_unit}"), f"{norm.replace('.', ',')} {axis_unit}"
    return value_code(text), text
