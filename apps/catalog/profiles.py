"""Deklaratyvūs per-kategoriją variantų option profiliai.

`_plan_variants` renkasi option ašis TIK iš profilio `axes` sąrašo –
kuri ašis yra pirkėjo pasirinkimas, yra domeno sprendimas per
kategoriją, ne kombinatorinis atskyrimo rinkinys. `context_rx` regex
taikomas tam pačiam lowercase kontekstui kaip CONTEXT_RULES
(_offer_context / _product_context). Pirmasis match'as laimi –
tvarka svarbi (specifiniai profiliai prieš bendresnius).

`composite_rules` (F3): tiekėjai deda kelis atributus į VIENĄ lauką
(MOSS `<ROZMER>` '0,08 mm / 1,2 kg / 100 m', Guru 'X-Small 20g',
'10g-1/0'). Taisyklės = eilės tvarka (token_regex_fragment,
kanoninis_kodas) poros; `split_composite` skaldo generic 'size'
reikšmę į token'us ('/'/'·'/'|' skirtukai + inline multi-token),
kiekvienas token'as match'inamas su taisyklėmis – pirma taisyklė
laimi. Tuščias kodas "" = token'as sąmoningai lieka liekanoje
(dviprasmiški reitingai kaip PE/'#'). Liekana eina į varianto
pavadinimą, ne į facets. `generic` profilis neturi taisyklių –
niekas neskaldoma bekategoriniams produktams.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .attributes import ATTRIBUTE_META, _LINES_CTX, _RODS_CTX


@dataclass(frozen=True)
class CategoryProfile:
    code: str
    context_rx: str
    axes: tuple[str, ...]
    # (token_regex_fragment, canonical_code) – eilės tvarka; "" kodas
    # pažymi token'ą kaip 'liekaną' (nekanonizuojamą) sąmoningai.
    composite_rules: tuple[tuple[str, str], ...] = ()


PROFILES: list[CategoryProfile] = [
    # Prieš rods: _RODS_CTX match'ina 'feeder'.
    CategoryProfile(
        "feeders",
        r"šėrykl|serykl|laidyn|kobr|raket|švin|svin|plummet|sinker|"
        r"\bleads?\b|feeder (cage|caged|method|open|window)",
        ("size", "weight"),
        composite_rules=(
            (r"g\b", "weight"),
            (r"x-?small|x-?large|small|medium|large", "size"),
        ),
    ),
    CategoryProfile(
        "clothing",
        r"marškin|marskin|džemper|dzemper|striuk|keln|šort|\bsortai|shorts?\b|"
        r"apranga|avalyn|clothing|apparel|hoodie|jacket|\btee\b|\bcap\b|"
        r"boots?\b|odzież|odziez|bekleidung",
        ("size", "color"),
    ),
    CategoryProfile(
        "floats",
        r"plūd|plud|float|waggler|\bposen?\b",
        ("weight", "size", "color"),
    ),
    CategoryProfile(
        "hooks",
        # 'hook lines' (MOSS pavadėlinių valų grupė) nėra kabliukai –
        # turi kristi į lines profilį (mm/kg/m).
        r"kabliuk|hooks?\b(?!\s+lines?\b)|haken|haczyk",
        ("hook_size", "weight", "size", "model"),
        # Džigo galvutės (MOSS 'Jigs and hooks' -> 'Galvakabliai',
        # kuriose 'kabliuk'): '10g-1/0' -> weight + hook_size.
        composite_rules=(
            (r"g\b|gr\b", "weight"),
            (r"#\s*\d+|roz\.?\s*\d+|\d+(?:/0)?(?![\d.,/a-zA-Z]|\s+[a-zA-Z])", "hook_size"),
        ),
    ),
    # Prieš bait: 'baitcasting' ritės/meškerės nėra jaukas.
    CategoryProfile(
        "reels",
        r"rit[ėe]s|ritė|reel|rolle|kołowrot|kolowrot",
        ("reel_size", "size", "model", "color"),
    ),
    # Prieš bait: 'Soft Baits' / 'Hard Baits' yra masalai, ne jaukas.
    CategoryProfile(
        "lures",
        r"vobler|wobbler|masal|guminuk|blizg|lures?\b|\bjig|shad|frog|spinner|"
        r"crank|popper|twister|(soft|hard|swim) ?baits?\b",
        ("size", "lure_size", "weight", "hook_size", "color", "pattern"),
        composite_rules=(
            (r"g\b|gr\b", "weight"),
            (r"cm|mm|in\b|[\"″']", "lure_size"),
            # '1/0', '4', '#8', 'roz.3' – kabliuko/spinnerio dydis.
            (r"#\d+|roz\.?\s*\d+|\d+(?:/0)?(?![\d.,/a-zA-Z]|\s+[a-zA-Z])", "hook_size"),
        ),
    ),
    CategoryProfile(
        "rigs",
        r"sistemėl|sistemel|\brigs?\b|przypon|vorfach|hair ?rig|chod",
        ("hook_size", "size", "line_diameter", "line_strength", "model"),
        composite_rules=(
            (r"#\d+|size\s*\d+|\d+(?:/0)?(?![\d.,/a-zA-Z]|\s+[a-zA-Z])", "hook_size"),
            (r"mm", "line_diameter"),
            # MOSS pavadėlių/sistemėlių ROZMER: '0,26mm 9kg 10m',
            # '#2/0 / 14cm / 20 kg'.
            (r"kg|lb", "line_strength"),
            (r"cm|inch|in\b|m\b", "line_length"),
            (r"g\b", "weight"),
        ),
    ),
    CategoryProfile(
        "bait",
        r"boil|pellet|jauk|groundbait|\bbaits?\b|liquid|\bdip\b|pop.?up|wafter",
        ("size", "weight", "volume", "flavour"),
    ),
    CategoryProfile(
        "nets",
        r"graibšt|graibst|\bnets?\b|podbier|landing|keepnet",
        ("size", "model", "unit_length"),
    ),
    CategoryProfile(
        "lines",
        _LINES_CTX,
        ("line_diameter", "color", "line_length"),
        composite_rules=(
            # PE / '#' reitingas dviprasmiškas -> liekana (varianto vardas).
            (r"pe|#", ""),
            (r"mm|diam", "line_diameter"),
            (r"kg|lb", "line_strength"),
            (r"m\b|meter", "line_length"),
            (r"lb", "line_strength"),
        ),
    ),
    CategoryProfile(
        "rods",
        _RODS_CTX,
        ("rod_length_m", "rod_length_ft", "testcurve", "casting_weight", "sections", "model"),
        composite_rules=(
            # sekcijos visomis feed kalbomis: PL '2 składy'/'1 część',
            # EN '2/3 parts'/'2pcs', DE '2 Teile', '+2T' (tip sekcijos).
            (r"skład|sklad|sekcj|sections?|pcs?|pieces?|parts?|teile|cz[ęe]ś|\+\s*\d", "sections"),
            (r"lbs?\b", "testcurve"),
            (r"g\b", "casting_weight"),
            (r"cm|m\b|ft", "rod_length_m"),
        ),
    ),
]

DEFAULT_PROFILE = CategoryProfile(
    "generic", "", ("model", "color", "size", "pattern")
)

for _p in [*PROFILES, DEFAULT_PROFILE]:
    for _ax in _p.axes:
        _meta = ATTRIBUTE_META.get(_ax)
        assert _meta and _meta.get("kind") == "axis", (
            f"profile '{_p.code}': '{_ax}' nėra axis tipo registre"
        )
    for _rx, _c in _p.composite_rules:
        if _c:
            _meta = ATTRIBUTE_META.get(_c)
            assert _meta and _meta.get("kind") == "axis", (
                f"profile '{_p.code}': composite '{_c}' nėra axis tipo registre"
            )


def profile_for(context: str) -> CategoryProfile:
    """Pirmas profilis, kurio regex match'ina kontekstą (lowercase)."""
    ctx = (context or "").lower()
    for profile in PROFILES:
        if profile.context_rx and re.search(profile.context_rx, ctx):
            return profile
    return DEFAULT_PROFILE


# ---- Kompozitinių reikšmių skaldymas (F3) ----

_BARE_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
# '/' nėra skirtukas kabliuko dydyje '1/0' ir slash-intervale '0.8/1.5'.
_SEP_RE = re.compile(r"[/·|]")
# '10g-1/0' -> '10g' + '1/0': '-' skelia kai kairė baigiasi raide,
# o dešinė – grynas skaičius (kabliuko dydis). '0.20mm-0.35mm' ir
# '10-40g' lieka neskilti (dešinėje vienetas), 'X-Small' – žodis.
_DASH_SPLIT_RE = re.compile(r"^(.*?[a-zA-ZµłŁ])\s*-\s*(\d+(?:/\d+)?)$")
# Gryni skyrybos ženklai token'uose – nėra reikšmė (ne į liekaną).
_PUNCT_TOKEN_RE = re.compile(r"[+x×\-–·.,/]+")
# Imperial vienetai token'e – dublikate pralaimi metriniam ('12ft/360cm').
_IMPERIAL_RE = re.compile(r"ft|in\b|inch|['\"″]", re.IGNORECASE)


def _split_segments(text: str) -> list[str]:
    parts: list[str] = []
    last = 0
    for m in _SEP_RE.finditer(text):
        if m.group(0) == "/":
            left = text[: m.start()].rstrip()
            right = text[m.end() :].lstrip()
            left_tok = left.split()[-1] if left.split() else ""
            if left_tok[-1:].isdigit() and re.match(r"0(?![\d.,])", right):
                continue  # kabliuko dydis '1/0', '10/0' (bet ne 'x/0,26')
            if _BARE_NUM_RE.fullmatch(left_tok) and right[:1].isdigit():
                continue  # '0.8/1.5' slash-intervalas (vienas matmuo)
        parts.append(text[last : m.start()])
        last = m.end()
    parts.append(text[last:])
    return [part.strip() for part in parts if part.strip()]


def _tokenize_segment(segment: str) -> list[str]:
    """Whitespace tokenai; plikas skaičius + vieneto žodis suliejami
    ('0,08 mm', '2 składy', '14 kg'). Po to '-' skilimas '10g-1/0'."""
    raw_tokens = segment.split()
    tokens: list[str] = []
    i = 0
    while i < len(raw_tokens):
        tok = raw_tokens[i]
        if (
            _BARE_NUM_RE.fullmatch(tok)
            and i + 1 < len(raw_tokens)
            and re.match(r"[a-zA-ZµłŁ#%\"'′″]", raw_tokens[i + 1])
        ):
            tokens.append(f"{tok} {raw_tokens[i + 1]}")
            i += 2
            continue
        tokens.append(tok)
        i += 1
    out: list[str] = []
    for tok in tokens:
        tok = tok.strip("()[]")
        if not tok:
            continue
        m = _DASH_SPLIT_RE.match(tok)
        if m:
            out.extend((m.group(1), m.group(2)))
        else:
            out.append(tok)
    return out


# Kodai, kurių taisyklė gali paimti token'ą be skaičiaus (dydžių žodžiai
# 'X-Small', liekanos-žymėjimas 'pe|#'). Matavimo kodams reikalingas
# bent vienas skaitmuo – 'Premium' neturi tapti line_length per 'm\b'.
_WORD_CODES = {"size", ""}


def split_composite(raw: str, profile: CategoryProfile) -> tuple[dict[str, str], str]:
    """returns ({canonical_code: value_substring}, leftover_joined)

    Splits on '/'/'·'/'|' separators AND inline multi-token ('X-Small 20g',
    '0,08 mm 1,2 kg 100 m').
    Each token is matched against rules in order; first rule wins per token.
    A token consumed by a rule is removed from leftover. Leftover tokens keep
    order, ' ' joined.
    """
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not text or not profile.composite_rules:
        return {}, text
    rules = [
        (re.compile(rx, re.IGNORECASE), code)
        for rx, code in profile.composite_rules
    ]
    produced: dict[str, str] = {}
    leftover: list[str] = []

    def _first_code(token: str) -> str | None:
        has_digit = any(ch.isdigit() for ch in token)
        for rx, code in rules:
            if code not in _WORD_CODES and not has_digit:
                continue
            if rx.search(token):
                return code
        return None

    for segment in _split_segments(text):
        for token in _tokenize_segment(segment):
            if _PUNCT_TOKEN_RE.fullmatch(token):
                continue  # grynas skirtukas/jungtukas – ne reikšmė
            code = _first_code(token)
            if code is None or not code:
                # None = neatpažintas; "" = taisyklė 'palik liekanoje'
                leftover.append(token)
            elif code not in produced:
                produced[code] = token
            elif _IMPERIAL_RE.search(produced[code]) and not _IMPERIAL_RE.search(
                token
            ):
                # '12ft/360cm' – tas pats matmuo; imame metrinį.
                produced[code] = token
            else:
                leftover.append(token)
    return produced, " ".join(leftover)
