from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from lxml import etree

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter


@register_adapter
class MossAdapter(BaseAdapter):
    code = "moss"

    # PL/DE feed'ai naudoja kitokią XML schemą (SK laukų pavadinimai).
    # Žemėlapis: alternatyvus laukas -> kanoninis (EN) laukas.
    _FIELD_ALIASES = {
        "ID_PRODUCT": "OC",
        "SKUPINA": "VARIANT",
        "PACKAGING": "BALENIE",
        "CENA_S_DPH_EU": "PRICE_VAT",
        "SKLADOVOST": "AVAILABLE",
    }

    def _item_to_row(self, item: etree._Element) -> dict[str, Any]:
        row: dict[str, Any] = {}
        for child in item:
            key = etree.QName(child).localname
            value = self._text(child.text)
            key = self._FIELD_ALIASES.get(key.upper(), key)
            if key == "SORTIMENT":
                key = "CATEGORYTEXT"
                value = self._normalize_sortiment(value)
            # IMGURL_* -> IMG*, VIDEO_* -> YOUTUBE* (dedup jau padarytas žemiau)
            upper = key.upper()
            if upper.startswith("IMGURL"):
                key = "IMG" + key[6:]
            elif upper.startswith("VIDEO"):
                key = "YOUTUBE" + key[5:]
            if key not in row:
                row[key] = value
                continue
            existing = self._text(row.get(key))
            if not existing and value:
                row[key] = value
        return row

    @staticmethod
    def _normalize_sortiment(value: str) -> str:
        """PL/DE 'SORTIMENT' naudoja ' | ' skirtuką; EN CATEGORYTEXT – '|||'."""
        if "|" in value and "|||" not in value:
            parts = [part.strip() for part in value.split("|") if part.strip()]
            return "|||".join(parts)
        return value

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        root = etree.fromstring(payload)
        for item in root.findall(".//SHOPITEM"):
            row = self._item_to_row(item)

            external_id = self._text(row.get("OC"))
            if not external_id:
                continue

            variant_parent = self._text(row.get("VARIANT"))
            product_name = self._text(row.get("PRODUCT"))
            variant_value = self._text(row.get("ROZMER"))
            package_value = self._text(row.get("BALENIE"))
            item_weight_value = self._text(row.get("HMOTNOST"))
            category_full_path = self._normalize_category_path(self._text(row.get("CATEGORYTEXT")))
            category_root, category_sub_path = self._split_category(category_full_path)

            images = self._collect_images(row)
            videos = self._collect_videos(row)
            weight_kg, dimensions_cm = self._extract_dimensions(row)
            shipping_height_cm = self._to_decimal(row.get("HEIGHT"))
            shipping_width_cm = self._to_decimal(row.get("WIDTH"))
            shipping_depth_cm = self._to_decimal(row.get("LENGTH"))
            shipping_weight_kg = self._decimal_to_kg_decimal(self._to_decimal(row.get("WEIGHT")))
            stock = self._to_int(row.get("AVAILABLE"))

            yield {
                "external_id": external_id,
                "sku": external_id,
                "name": product_name or external_id,
                "base_name": product_name,
                "ean": self._text(row.get("EAN")),
                "brand_name": self._text(row.get("ZNACKA")),
                "category_path": category_root,
                "description": self._text(row.get("DESCRIPTION")),
                "images": images,
                "videos": videos,
                "stock": stock,
                "purchase_price": self._to_decimal(row.get("PRICE_VAT")),
                "currency": "EUR",
                "is_available": stock > 0,
                "attributes": {
                    "variant_value": variant_value,
                    "package_value": package_value,
                    "item_weight_value": item_weight_value,
                    "weight_kg": weight_kg,
                    "dimensions_cm": dimensions_cm,
                },
                "extra": {
                    "product_group": variant_parent or external_id,
                    "variant_parent_external_id": variant_parent,
                    "is_default_variant": not bool(variant_parent),
                    "variant_value": variant_value,
                    "size_raw": variant_value,
                    "package_raw": package_value,
                    "unit_weight_raw": item_weight_value,
                    "sub_category_raw": category_sub_path,
                    "category_path_full": category_full_path,
                    "shipping_height_cm": shipping_height_cm,
                    "shipping_width_cm": shipping_width_cm,
                    "shipping_depth_cm": shipping_depth_cm,
                    "shipping_weight_kg": shipping_weight_kg,
                    "main_image_url": images[0] if images else "",
                },
                "raw": row,
            }

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        external_id = self._text(row.get("external_id") or row.get("OC"))
        if not external_id:
            raise AdapterError(f"MOSS: trūksta OC reikšmės eilutėje: {repr(row)[:200]}")

        name = self._text(row.get("name") or row.get("PRODUCT"))
        if not name:
            raise AdapterError(f"MOSS: trūksta PRODUCT pavadinimo (OC={external_id})")

        base_attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
        filtered_attributes = {key: value for key, value in base_attributes.items() if value not in ("", None, {})}

        base_extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        extra = {
            "product_group": self._text(base_extra.get("product_group")),
            "variant_parent_external_id": self._text(base_extra.get("variant_parent_external_id")),
            "is_default_variant": bool(base_extra.get("is_default_variant")),
            "variant_value": self._text(base_extra.get("variant_value")),
            "size_raw": self._text(base_extra.get("size_raw")),
            "package_raw": self._text(base_extra.get("package_raw")),
            "unit_weight_raw": self._text(base_extra.get("unit_weight_raw")),
            "sub_category_raw": self._text(base_extra.get("sub_category_raw")),
            "category_path_full": self._text(base_extra.get("category_path_full")),
            "shipping_height_cm": self._text(base_extra.get("shipping_height_cm")),
            "shipping_width_cm": self._text(base_extra.get("shipping_width_cm")),
            "shipping_depth_cm": self._text(base_extra.get("shipping_depth_cm")),
            "shipping_weight_kg": self._text(base_extra.get("shipping_weight_kg")),
            "base_name": self._text(row.get("base_name")),
            "main_image_url": self._text(base_extra.get("main_image_url")),
        }

        stock = self._to_int(row.get("stock"))
        if stock <= 0:
            stock = self._to_int(row.get("AVAILABLE"))

        return NormalizedItem(
            external_id=external_id,
            sku=self._text(row.get("sku")) or external_id,
            name=name,
            ean=self._text(row.get("ean") or row.get("EAN")),
            brand_name=self._text(row.get("brand_name") or row.get("ZNACKA")),
            category_path=self._text(row.get("category_path")),
            description=self._text(row.get("description") or row.get("DESCRIPTION")),
            attributes=filtered_attributes,
            images=self._clean_images(row.get("images")),
            videos=self._clean_videos(row.get("videos")),
            stock=max(stock, 0),
            purchase_price=self._to_decimal(row.get("purchase_price") or row.get("PRICE_VAT")),
            currency="EUR",
            is_available=max(stock, 0) > 0,
            extra=extra,
            raw=self._clean_dict(row.get("raw", row)),
        )

    def _collect_images(self, row: dict[str, Any]) -> list[str]:
        image_values: list[str] = []
        for key, value in row.items():
            if not key.upper().startswith("IMG"):
                continue
            if isinstance(value, list):
                image_values.extend([self._text(v) for v in value])
            else:
                image_values.append(self._text(value))

        deduplicated: list[str] = []
        seen: set[str] = set()
        for image in image_values:
            if not image:
                continue
            if image in seen:
                continue
            seen.add(image)
            deduplicated.append(image)
        return deduplicated

    # YOUTUBE, YOUTUBE_2, ... – MOSS feed'e saugomas tik YouTube video ID.
    # Normalizuojama į pilną youtu.be URL – toks pats formatas kaip FOX feed'uose.
    @staticmethod
    def _youtube_id_to_url(video_id: str) -> str:
        if video_id.lower().startswith(("http://", "https://")):
            return video_id
        return f"https://youtu.be/{video_id}"

    def _collect_videos(self, row: dict[str, Any]) -> list[dict[str, str]]:
        videos: list[dict[str, str]] = []
        seen: set[str] = set()
        for key, value in row.items():
            if not str(key).upper().startswith("YOUTUBE"):
                continue
            video_id = self._text(value)
            if not video_id:
                continue
            url = self._youtube_id_to_url(video_id)
            if url in seen:
                continue
            seen.add(url)
            videos.append({"url": url, "type": "product", "language": ""})
        return videos

    def _clean_videos(self, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        cleaned: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            url = self._text(item.get("url"))
            if not url:
                continue
            cleaned.append(
                {
                    "url": url,
                    "type": self._text(item.get("type")) or "product",
                    "language": self._text(item.get("language")),
                }
            )
        return cleaned

    def _extract_dimensions(self, row: dict[str, Any]) -> tuple[float | None, dict[str, float]]:
        weight_raw = self._to_decimal(row.get("WEIGHT"))
        weight_kg = self._decimal_to_kg(weight_raw)

        dimensions_cm: dict[str, float] = {}
        for source_key, output_key in (
            ("WIDTH", "width"),
            ("LENGTH", "length"),
            ("HEIGHT", "height"),
        ):
            value = self._to_decimal(row.get(source_key))
            as_float = self._decimal_to_float(value)
            if as_float is not None:
                dimensions_cm[output_key] = as_float
        return weight_kg, dimensions_cm

    @staticmethod
    def _normalize_category_path(value: str) -> str:
        if not value:
            return ""
        parts = [part.strip() for part in value.split("|||") if part.strip()]
        return " > ".join(parts)

    @staticmethod
    def _category_parts(category_path: str) -> list[str]:
        return [part.strip() for part in category_path.split(">") if part.strip()]

    @classmethod
    def _split_category(cls, category_path: str) -> tuple[str, str]:
        parts = cls._category_parts(category_path)
        if not parts:
            return "", ""
        return parts[0], " > ".join(parts[1:])

    @staticmethod
    def _decimal_to_kg(value: Decimal | None) -> float | None:
        if value is None:
            return None
        return float(value / Decimal("1000"))

    @staticmethod
    def _decimal_to_kg_decimal(value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return value / Decimal("1000")

    @staticmethod
    def _decimal_to_float(value: Decimal | None) -> float | None:
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _clean_images(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [self._text(item) for item in value if self._text(item)]

    @staticmethod
    def _clean_dict(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {str(key): item for key, item in value.items()}
