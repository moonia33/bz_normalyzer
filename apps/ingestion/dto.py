from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass
class NormalizedItem:
    """Vienoda vidinė prekės forma visiems adapteriams."""

    external_id: str
    name: str
    ean: str = ""
    sku: str = ""
    brand_name: str = ""
    category_path: str = ""
    description: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    # [{"url": ..., "type": "product|instructional|tutorial", "language": "en"}]
    videos: list[dict[str, str]] = field(default_factory=list)
    stock: int = 0
    availability_date: date | None = None
    purchase_price: Decimal | None = None
    currency: str = "EUR"
    is_available: bool = True
    extra: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
