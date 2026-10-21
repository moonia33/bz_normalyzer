"""Atsarginių dalių (spare parts) atpažinimas iš tiekėjo offer'io.

Tiekėjų kategorijų pavadinimai, kuriuose guli dalys, o ne prekės:
shimano 'Reel parts' / 'Spools' / 'Rod Sections (excl poles)',
korda 'Leaders & spares' / 'Aventus Pole Topkit/Spares' /
'Carp Porter Spare Parts' / 'Rive Spare parts'.
Fox tokios kategorijos neturi – dalys randamos pagal pavadinimą
('... spare spool ...' po 'Reels'/'Accessories').
"""

import re

PART_CATEGORY_RX = re.compile(
    r"reel.?parts?|spools?|rod.?sections?|spares?\b|spare.?parts?|topkit.?spares?",
    re.I,
)
PART_NAME_RX = re.compile(
    r"\b(spare|replacement)\s+(spool|drag|handle|bail|arm)\b|\bspare\s+spool\b",
    re.I,
)


def is_part(offer) -> bool:
    """Ar offer'io eilutė yra atsarginė dalis, o ne parduodama prekė."""
    category_bits = [
        getattr(offer, "category_raw", "") or "",
        getattr(offer, "sub_category_raw", "") or "",
    ]
    product = getattr(offer, "product", None)
    if product is not None:
        category_bits.extend(
            [
                getattr(product, "category_raw", "") or "",
                getattr(product, "sub_category_raw", "") or "",
            ]
        )
    if any(PART_CATEGORY_RX.search(bit) for bit in category_bits if bit):
        return True

    names = [getattr(offer, "name", "") or ""]
    if product is not None:
        names.append(getattr(product, "name", "") or "")
    return any(PART_NAME_RX.search(name) for name in names if name)


def product_is_part(offers) -> bool:
    """Ar produktas = atsarginė dalis.

    Taisyklė: >= 50% produkto eksportuojamų offer'ių yra dalys.
    1 iš 2 variantų = lygiai pusė -> True (daliai laimi riba).
    """
    offers = list(offers)
    if not offers:
        return False
    part_count = sum(1 for offer in offers if is_part(offer))
    return part_count * 2 >= len(offers)
