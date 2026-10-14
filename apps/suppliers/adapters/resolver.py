from __future__ import annotations

from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import get_adapter
from apps.suppliers.models import SourceKind, SupplierFeed

GENERIC_BY_KIND = {
    SourceKind.XML_URL: "generic_xml",
    SourceKind.JSON_URL: "generic_json",
    SourceKind.CSV_URL: "generic_excel",
    SourceKind.EXCEL_UPLOAD: "generic_excel",
    SourceKind.CSV_UPLOAD: "generic_excel",
}


def resolve_adapter(feed: SupplierFeed) -> BaseAdapter:
    codes = [
        feed.adapter_code,
        feed.supplier.code,
        GENERIC_BY_KIND.get(feed.kind, ""),
    ]
    for code in codes:
        if not code:
            continue
        adapter_cls = get_adapter(code)
        if adapter_cls:
            return adapter_cls(feed)
    raise AdapterError(
        f"Nerastas adapteris šaltiniui {feed}. "
        "Sukurkite klasę su @register_adapter ir code=tiekėjo kodas, "
        "arba naudokite generic_* su config.fields."
    )
