from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from lxml import etree

from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter


@register_adapter
class GenericXmlAdapter(BaseAdapter):
    """Universalus XML. Config pavyzdys:

    {
      "item_xpath": "//product",
      "ns": {"ns": "http://example.com"},
      "fields": {
        "external_id": "id",
        "name": "title",
        "purchase_price": "price",
        "category_path": "category"
      }
    }

    Laukai imami kaip vaiko elemento tekstas arba atributas @id.
    """

    code = "generic_xml"

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        item_xpath = self.config.get("item_xpath")
        if not item_xpath:
            raise AdapterError("generic_xml reikalauja config.item_xpath")
        namespaces = self.config.get("ns") or {}
        root = etree.fromstring(payload)
        for node in root.xpath(item_xpath, namespaces=namespaces):
            yield self._element_to_row(node, namespaces)

    def _element_to_row(self, node, namespaces: dict[str, str]) -> dict[str, Any]:
        row: dict[str, Any] = dict(node.attrib)
        for child in node:
            tag = etree.QName(child).localname
            row[tag] = (child.text or "").strip()
        fields: dict[str, str] = self.config.get("fields") or {}
        for logical, source in fields.items():
            if source.startswith("@"):
                row[source] = node.get(source[1:], "")
            elif source.startswith("./") or source.startswith(".//") or "/" in source:
                found = node.xpath(source, namespaces=namespaces)
                if found and hasattr(found[0], "text"):
                    row[source] = (found[0].text or "").strip()
                elif found:
                    row[source] = str(found[0])
            row.setdefault(logical, row.get(source, ""))
        return row
