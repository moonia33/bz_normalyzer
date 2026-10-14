from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter


def _dig(data: Any, path: str) -> Any:
    current = data
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


@register_adapter
class GenericJsonAdapter(BaseAdapter):
    """Universalus JSON. Config: {"items_path": "data.items", "fields": {...}}."""

    code = "generic_json"

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        data = json.loads(payload.decode("utf-8-sig"))
        items_path = self.config.get("items_path", "")
        items = _dig(data, items_path) if items_path else data
        if isinstance(items, dict):
            items = items.values()
        if not isinstance(items, list) and not hasattr(items, "__iter__"):
            raise AdapterError("JSON šaknyje nerastas prekių sąrašas. Nustatykite items_path.")
        for item in items:
            if isinstance(item, dict):
                yield item
