"""Deklaratyvūs per-kategoriją variantų option profiliai.

`_plan_variants` renkasi option ašis TIK iš profilio `axes` sąrašo –
kuri ašis yra pirkėjo pasirinkimas, yra domeno sprendimas per
kategoriją, ne kombinatorinis atskyrimo rinkinys. `context_rx` regex
taikomas tam pačiam lowercase kontekstui kaip CONTEXT_RULES
(_offer_context / _product_context). Pirmasis match'as laimi –
tvarka svarbi (specifiniai profiliai prieš bendresnius).
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


PROFILES: list[CategoryProfile] = [
    # Prieš rods: _RODS_CTX match'ina 'feeder'.
    CategoryProfile(
        "feeders",
        r"šėrykl|serykl|laidyn|kobr|raket|švin|svin|plummet|sinker|"
        r"\bleads?\b|feeder (cage|caged|method|open|window)",
        ("size", "weight"),
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
        ("weight", "size"),
    ),
    CategoryProfile(
        "hooks",
        r"kabliuk|hooks?\b|haken|haczyk",
        ("hook_size", "size", "model"),
    ),
    # Prieš bait: 'baitcasting' ritės/meškerės nėra jaukas.
    CategoryProfile(
        "reels",
        r"rit[ėe]s|ritė|reel|rolle|kołowrot|kolowrot",
        ("reel_size", "size", "model", "color"),
    ),
    CategoryProfile(
        "bait",
        r"boil|pellet|jauk|groundbait|\bbait(?!cast)|liquid|\bdip\b|pop.?up|wafter",
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
    ),
    CategoryProfile(
        "lures",
        r"vobler|wobbler|masal|guminuk|blizg|lures?\b|\bjig|shad|frog|spinner|"
        r"crank|popper|twister|softbait|hardbait",
        ("size", "lure_size", "weight", "color", "pattern"),
    ),
    CategoryProfile(
        "rods",
        _RODS_CTX,
        ("rod_length_m", "testcurve", "casting_weight", "sections", "model"),
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


def profile_for(context: str) -> CategoryProfile:
    """Pirmas profilis, kurio regex match'ina kontekstą (lowercase)."""
    ctx = (context or "").lower()
    for profile in PROFILES:
        if profile.context_rx and re.search(profile.context_rx, ctx):
            return profile
    return DEFAULT_PROFILE
