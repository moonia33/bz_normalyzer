from __future__ import annotations

from collections.abc import Iterable, Iterator
from decimal import Decimal, InvalidOperation
from typing import Any

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.models import SupplierFeed


class AdapterError(Exception):
    pass


class BaseAdapter:
    """Bazinė klasė tiekėjo adapteriui.

    Naujas tiekėjas: paveldėkite, nustatykite `code`, implementuokite
    `parse` ir (jei reikia) `fetch` / `normalize`. Užregistruokite su
    @register_adapter. Generic XML/JSON/Excel tinka paprastiems feed'ams.
    """

    code: str = ""

    def __init__(self, feed: SupplierFeed):
        self.feed = feed
        self.config: dict[str, Any] = feed.config or {}

    def fetch(self, uploaded_path: str | None = None) -> bytes:
        if uploaded_path:
            with open(uploaded_path, "rb") as handle:
                return handle.read()
        if self.feed.file:
            self.feed.file.open("rb")
            try:
                return self.feed.file.read()
            finally:
                self.feed.file.close()
        if self.feed.url:
            return self._fetch_url(self.feed.url)
        raise AdapterError(
            f"Šaltinis {self.feed} neturi nei URL, nei failo, nei įkelto dokumento."
        )

    def _fetch_url(self, url: str) -> bytes:
        import requests

        headers = self.config.get("headers") or {}
        timeout = int(self.config.get("timeout", 60))
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.content

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        raise NotImplementedError

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        mapping: dict[str, str] = self.config.get("fields") or {}

        def pick(key: str, default: str = "") -> Any:
            source = mapping.get(key, key)
            value = row.get(source, default)
            return default if value is None else value

        price = self._to_decimal(pick("purchase_price"))
        stock = self._to_int(pick("stock", 0))
        external_id = str(pick("external_id") or "").strip()
        name = str(pick("name") or "").strip()
        if not external_id:
            raise AdapterError(f"Trūksta external_id eilutėje: {repr(row)[:200]}")
        if not name:
            raise AdapterError(f"Trūksta name (external_id={external_id})")

        images = pick("images", [])
        if isinstance(images, str):
            images = [part.strip() for part in images.split(",") if part.strip()]

        available_raw = pick("is_available", True)
        if isinstance(available_raw, str):
            available = available_raw.strip().lower() not in {"0", "false", "no", "n", ""}
        else:
            available = bool(available_raw)

        return NormalizedItem(
            external_id=external_id,
            name=name,
            ean=str(pick("ean") or "").strip(),
            sku=str(pick("sku") or "").strip(),
            brand_name=str(pick("brand_name") or "").strip(),
            category_path=str(pick("category_path") or "").strip(),
            description=str(pick("description") or "").strip(),
            attributes=pick("attributes") if isinstance(pick("attributes"), dict) else {},
            images=list(images) if isinstance(images, list) else [],
            stock=stock,
            purchase_price=price,
            currency=str(pick("currency") or "EUR").strip() or "EUR",
            is_available=available,
            extra={k: v for k, v in row.items() if k not in {"raw"}},
            raw=row,
        )

    def iter_items(self, payload: bytes) -> Iterable[NormalizedItem]:
        for row in self.parse(payload):
            yield self.normalize(row)

    @staticmethod
    def _to_decimal(value: Any) -> Decimal | None:
        if value in (None, ""):
            return None
        if isinstance(value, Decimal):
            return value

        text = str(value).strip()
        if not text:
            return None

        negative = text.startswith("(") and text.endswith(")")
        if negative:
            text = text[1:-1].strip()

        text = text.replace("\u00a0", "").replace(" ", "")
        text = text.replace("€", "").replace("£", "").replace("$", "")
        text = text.replace("\u202f", "")
        text = text.rstrip("%")

        if text.startswith("+"):
            text = text[1:]

        if text.startswith("-"):
            negative = True
            text = text[1:]

        if text.count(",") and text.count("."):
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            parts = text.split(",")
            if len(parts) > 1 and len(parts[-1]) == 3 and "." not in text:
                text = "".join(parts)
            elif len(parts) > 1 and len(parts[-1]) in {1, 2}:
                text = ".".join(parts)
            else:
                text = "".join(parts)

        text = "".join(ch for ch in text if ch.isdigit() or ch in {".", "-"})
        if not text or text == ".":
            return None

        if negative:
            text = f"-{text.lstrip('-')}"

        try:
            return Decimal(text)
        except InvalidOperation:
            return None

    @staticmethod
    def _to_int(value: Any) -> int:
        if value in (None, ""):
            return 0
        decimal_value = BaseAdapter._to_decimal(value)
        if decimal_value is None:
            return 0
        try:
            return int(decimal_value)
        except (InvalidOperation, ValueError, OverflowError):
            return 0
