from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from openpyxl import load_workbook

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter


@register_adapter
class FoxAdapter(BaseAdapter):
    code = "fox"

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        workbook = load_workbook(io.BytesIO(payload), read_only=True, data_only=True)

        base_rows = self._sheet_rows(workbook, "BASE")
        text_rows = self._sheet_index(workbook, "TEXT")
        media_cloud_rows = self._sheet_index(workbook, "MEDIA CLOUD LINKS")
        specs_rows = self._sheet_index(workbook, "SPECS")

        group_brand_map: dict[str, str] = {}
        group_base_name_map: dict[str, str] = {}
        for row in base_rows:
            group_code = self._text(row.get("Product Group"))
            brand = self._text(row.get("Brand"))
            if group_code and brand and group_code not in group_brand_map:
                group_brand_map[group_code] = brand
        for row in text_rows.values():
            group_code = self._text(row.get("Product Group"))
            base_name = self._text(row.get("Product Description"))
            if group_code and base_name and group_code not in group_base_name_map:
                group_base_name_map[group_code] = base_name

        for base in base_rows:
            art_no = self._text(base.get("Art-No."))
            if not art_no:
                continue

            product_group = self._text(base.get("Product Group"))
            text_row = text_rows.get(art_no, {})
            specs_row = specs_rows.get(art_no, {})
            media_cloud_row = media_cloud_rows.get(art_no, {})

            variant_name = self._text(base.get("Name"))
            base_name = self._text(text_row.get("Product Description")) or group_base_name_map.get(
                product_group,
                "",
            )
            brand_name = (
                self._text(base.get("Brand"))
                or self._text(text_row.get("Brand"))
                or group_brand_map.get(product_group, "")
            )

            category_name = self._text(base.get("Category"))
            sub_category = self._text(base.get("Sub Category"))
            family = self._text(base.get("Family"))

            images = self._extract_media_links(media_cloud_row)
            main_image_url = images[0] if images else ""

            technical_description = self._text(text_row.get("Techn. Description"))
            combined_full_text = self._text(text_row.get("Combined Full Text"))

            specs_attributes = self._specs_attributes(specs_row)
            shipping_dimensions = self._shipping_dimensions(specs_row)
            size_raw = self._text(specs_row.get("Size"))
            shipping_height_cm = self._to_decimal(specs_row.get("Retail Height cm"))
            shipping_width_cm = self._to_decimal(specs_row.get("Retail Width cm"))
            shipping_depth_cm = self._to_decimal(specs_row.get("Retail Depth cm"))
            shipping_weight_kg = self._grams_to_kg(specs_row.get("Retail Weight gram"))

            identifiers = {
                "art_no": art_no,
                "product_group": product_group,
                "barcode_single_ean": self._text(base.get("Barcode Single EAN")),
                "barcode_tradepack_ean": self._text(base.get("Barcode Tradepack EAN")),
            }

            raw_joined = {
                "base": self._clean_dict(base),
                "text": self._clean_dict(text_row),
                "specs": self._clean_dict(specs_row),
                "media_cloud_links": self._clean_dict(media_cloud_row),
            }

            yield {
                "external_id": art_no,
                "sku": art_no,
                "name": variant_name or base_name or art_no,
                "base_name": base_name,
                "description": combined_full_text or base_name or variant_name,
                "technical_description": technical_description,
                "combined_full_text": combined_full_text,
                "brand_name": brand_name,
                "category_path": category_name,
                "sub_category_raw": sub_category,
                "family_raw": family,
                "ean": identifiers["barcode_single_ean"],
                "purchase_price": self._to_decimal(base.get("Trade EUR")),
                "rrp_price": self._to_decimal(base.get("RRP EUR")),
                "currency": "EUR",
                "stock": 0,
                "images": images,
                "main_image_url": main_image_url,
                "attributes": {
                    "specs": specs_attributes,
                    "shipping_dimensions": shipping_dimensions,
                },
                "identifiers": identifiers,
                "size_raw": size_raw,
                "shipping_height_cm": shipping_height_cm,
                "shipping_width_cm": shipping_width_cm,
                "shipping_depth_cm": shipping_depth_cm,
                "shipping_weight_kg": shipping_weight_kg,
                "raw": raw_joined,
            }

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        external_id = self._text(row.get("external_id"))
        name = self._text(row.get("name"))
        if not external_id:
            raise AdapterError(f"FOX: trūksta Art-No. eilutėje: {repr(row)[:200]}")
        if not name:
            raise AdapterError(f"FOX: trūksta Name (Art-No.={external_id})")

        attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
        extra = {
            "identifiers": row.get("identifiers", {}),
            "base_name": self._text(row.get("base_name")),
            "size_raw": self._text(row.get("size_raw")),
            "shipping_height_cm": self._text(row.get("shipping_height_cm")),
            "shipping_width_cm": self._text(row.get("shipping_width_cm")),
            "shipping_depth_cm": self._text(row.get("shipping_depth_cm")),
            "shipping_weight_kg": self._text(row.get("shipping_weight_kg")),
            "product_group": self._text(row.get("identifiers", {}).get("product_group"))
            if isinstance(row.get("identifiers"), dict)
            else "",
            "sub_category_raw": self._text(row.get("sub_category_raw")),
            "family_raw": self._text(row.get("family_raw")),
            "main_image_url": self._text(row.get("main_image_url")),
            "technical_description": self._text(row.get("technical_description")),
            "combined_full_text": self._text(row.get("combined_full_text")),
            "rrp_price": self._to_decimal(row.get("rrp_price")),
        }

        filtered_attributes = self._filter_attributes(attributes)

        return NormalizedItem(
            external_id=external_id,
            sku=self._text(row.get("sku")) or external_id,
            name=name,
            ean=self._text(row.get("ean")),
            brand_name=self._text(row.get("brand_name")),
            category_path=self._text(row.get("category_path")),
            description=self._text(row.get("description")),
            attributes=filtered_attributes,
            images=self._clean_images(row.get("images")),
            stock=0,
            purchase_price=self._to_decimal(row.get("purchase_price")),
            currency=self._text(row.get("currency")) or "EUR",
            is_available=True,
            extra=self._clean_dict(extra),
            raw=self._clean_dict(row.get("raw", {})),
        )

    def _sheet_rows(self, workbook, sheet_name: str) -> list[dict[str, Any]]:
        if sheet_name not in workbook.sheetnames:
            return []
        ws = workbook[sheet_name]
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if header is None:
            return []
        headers = [self._text(value) for value in header]
        result: list[dict[str, Any]] = []
        for row in rows:
            if not any(cell not in (None, "") for cell in row):
                continue
            item: dict[str, Any] = {}
            for index, value in enumerate(row):
                if index >= len(headers):
                    break
                key = headers[index]
                if not key:
                    continue
                item[key] = value
            if item:
                result.append(item)
        return result

    def _sheet_index(self, workbook, sheet_name: str) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for row in self._sheet_rows(workbook, sheet_name):
            art_no = self._text(row.get("Art-No."))
            if art_no:
                indexed[art_no] = row
        return indexed

    def _extract_media_links(self, media_row: dict[str, Any]) -> list[str]:
        urls: list[str] = []
        for key, value in media_row.items():
            if key == "Art-No.":
                continue
            text = self._text(value)
            if text.lower().startswith("http://") or text.lower().startswith("https://"):
                urls.append(text)
        seen: set[str] = set()
        ordered: list[str] = []
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            ordered.append(url)
        return ordered

    def _specs_attributes(self, specs_row: dict[str, Any]) -> dict[str, Any]:
        if not specs_row:
            return {}
        capture = False
        result: dict[str, Any] = {}
        for key, value in specs_row.items():
            if key == "Unit Height":
                capture = True
            if not capture:
                continue
            clean = self._clean_value(value)
            if clean in (None, ""):
                continue
            result[key] = clean
        return result

    def _shipping_dimensions(self, specs_row: dict[str, Any]) -> dict[str, Any]:
        dim_keys = [
            "Retail Height cm",
            "Retail Width cm",
            "Retail Depth cm",
            "Retail Weight gram",
        ]
        result: dict[str, Any] = {}
        for key in dim_keys:
            value = self._clean_value(specs_row.get(key))
            if value in (None, ""):
                continue
            result[key] = value
        return result

    def _grams_to_kg(self, value: Any) -> Decimal | None:
        grams = self._to_decimal(value)
        if grams is None:
            return None
        return grams / Decimal("1000")

    @staticmethod
    def _filter_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
        cleaned = {} if not attributes else {str(key): value for key, value in attributes.items()}
        allowed = {"specs", "shipping_dimensions"}
        for key in list(cleaned):
            if key not in allowed:
                cleaned.pop(key, None)
        return cleaned

    def _clean_images(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [self._text(item) for item in value if self._text(item)]
        return []

    def _clean_dict(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            cleaned[str(key)] = self._clean_value(item)
        return cleaned

    def _clean_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, list):
            return [self._clean_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._clean_value(item) for item in value]
        if isinstance(value, dict):
            return {str(k): self._clean_value(v) for k, v in value.items()}
        return str(value)

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()
