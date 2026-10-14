from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter


@register_adapter
class FoxStockAdapter(BaseAdapter):
    code = "fox_stock"

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        text = payload.decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text), delimiter=self._detect_delimiter(text))
        for row in reader:
            values = [str(cell).strip() for cell in row]
            if not values or not any(values):
                continue
            if self._looks_like_header(values):
                continue
            external_id = values[0]
            if not external_id:
                continue
            status = values[1] if len(values) > 1 else ""
            raw_date = values[2] if len(values) > 2 else ""
            barcode = values[3] if len(values) > 3 else ""
            availability_date = self._parse_date(raw_date)
            is_available = self._is_available(status, availability_date)
            stock = 10 if is_available else 0
            yield {
                "external_id": external_id,
                "sku": external_id,
                "name": external_id,
                "status": status,
                "raw_date": raw_date,
                "ean": barcode,
                "stock": stock,
                "is_available": is_available,
                "availability_date": availability_date,
                "brand_name": "",
                "category_path": "",
                "description": "",
                "images": [],
                "raw": {
                    "status": status,
                    "raw_date": raw_date,
                    "barcode": barcode,
                },
            }

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        external_id = self._text(row.get("external_id") or row.get("sku") or row.get("Art-No."))
        if not external_id:
            raise AdapterError(f"FOX stock: trūksta external_id eilutėje: {repr(row)[:200]}")

        status = self._text(row.get("status") or row.get("stock_status") or row.get("availability") or "")
        availability_raw = row.get("availability_date") or row.get("date") or row.get("raw_date") or ""
        if isinstance(availability_raw, date):
            availability_date = availability_raw
            raw_date = availability_date.isoformat()
        else:
            raw_date = self._text(availability_raw)
            availability_date = self._parse_date(raw_date)
        if isinstance(row.get("is_available"), bool):
            is_available = bool(row.get("is_available"))
        else:
            is_available = self._is_available(status, availability_date)
        stock = 10 if is_available else 0
        ean = self._text(row.get("ean") or row.get("barcode") or row.get("barcode_single_ean"))

        return NormalizedItem(
            external_id=external_id,
            sku=self._text(row.get("sku")) or external_id,
            name=self._text(row.get("name")) or external_id,
            ean=ean,
            brand_name=self._text(row.get("brand_name")),
            category_path=self._text(row.get("category_path")),
            description=self._text(row.get("description")),
            attributes={},
            images=[],
            stock=stock,
            availability_date=availability_date,
            purchase_price=None,
            currency="EUR",
            is_available=is_available,
            extra={
                "status": status,
                "raw_date": raw_date,
                "is_available": is_available,
                "partial_update": True,
                "source": "fox_stock",
            },
            raw=self._clean_dict(row.get("raw", {})),
        )

    @staticmethod
    def _looks_like_header(values: list[str]) -> bool:
        lowered = [value.lower() for value in values[:4]]
        return any(
            item in lowered
            for item in {"art-no", "art-no.", "art no", "sku", "item", "product", "status", "available"}
        )

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
    def _parse_date(value: str) -> date | None:
        text = str(value or "").strip()
        if not text or text in {"-", "n/a", "na"}:
            return None
        for pattern in ("%d-%m-%y", "%d-%m-%Y", "%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, pattern).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _is_available(status: str, availability_date: date | None) -> bool:
        normalized = str(status).strip().upper()
        if availability_date is not None:
            return False
        return normalized in {"Y", "YES", "TRUE", "1", "AVAILABLE"}

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
