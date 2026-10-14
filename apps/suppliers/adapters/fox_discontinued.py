from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date
from typing import Any

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter


@register_adapter
class FoxDiscontinuedAdapter(BaseAdapter):
    code = "fox_discontinued"

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        text = payload.decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text), delimiter=self._detect_delimiter(text))
        for row in reader:
            values = [str(cell).strip() for cell in row]
            if not values or not any(values):
                continue
            if self._looks_like_header(values):
                continue
            external_id = values[0] if values else ""
            if not external_id:
                continue
            yield {
                "external_id": external_id,
                "sku": external_id,
                "name": external_id,
                "brand_name": "",
                "category_path": "",
                "description": "Discontinued",
                "images": [],
                "stock": 0,
                "is_available": False,
                "availability_date": None,
                "purchase_price": None,
                "currency": "EUR",
                "raw": {"source": "discontinued", "row": values},
            }

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        external_id = self._text(row.get("external_id") or row.get("sku") or row.get("Art-No."))
        if not external_id:
            raise AdapterError(f"FOX discontinued: trūksta external_id eilutėje: {repr(row)[:200]}")

        return NormalizedItem(
            external_id=external_id,
            sku=self._text(row.get("sku")) or external_id,
            name=self._text(row.get("name")) or external_id,
            ean="",
            brand_name=self._text(row.get("brand_name")),
            category_path=self._text(row.get("category_path")),
            description=self._text(row.get("description")) or "Discontinued",
            attributes={},
            images=[],
            stock=0,
            availability_date=None,
            purchase_price=None,
            currency=self._text(row.get("currency")) or "EUR",
            is_available=False,
            extra={
                "status": "discontinued",
                "is_available": False,
                "partial_update": True,
                "source": "fox_discontinued",
            },
            raw=self._clean_dict(row.get("raw", {})),
        )

    @staticmethod
    def _looks_like_header(values: list[str]) -> bool:
        lowered = [value.lower() for value in values[:4]]
        return any(item in lowered for item in {"art-no", "art-no.", "art no", "sku", "item", "product", "code"})

    @staticmethod
    def _detect_delimiter(text: str) -> str:
        sample = text[:4096]
        if not sample:
            return ","
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            return dialect.delimiter
        except csv.Error:
            return ";" if ";" in sample and "," not in sample else ","

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _clean_dict(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {str(k): v for k, v in value.items()}
