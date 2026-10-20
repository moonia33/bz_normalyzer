"""Nemapintų / įtartinų atributų kolektorius eksporto metu.

`build_catalog_payload` apgaubiamas `with collect():` – viduje kviečiamas
`report()` kaupia įėjimo eilutes, o kontekstui pasibaigus jos upsert'inamos
į `UnmappedAttribute` (pilnas snapshot'as: count/sample_values perrašomi).
Be aktyvaus konteksto `report()` yra no-op (testai, ad-hoc kvietimai).
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

_state = threading.local()

_MAX_SAMPLES = 10


@contextmanager
def collect():
    """Aktyvuoja kolektorių; išeinant bulk-upsert'ina UnmappedAttribute."""
    bucket: dict[tuple, dict] = {}
    prev = getattr(_state, "bucket", None)
    _state.bucket = bucket
    try:
        yield bucket
    finally:
        _state.bucket = prev
        if prev is None:
            _flush(bucket)


def report(
    supplier: str | None,
    code: str,
    label: str = "",
    kind: str = "unmapped_code",
    category_raw: str = "",
    value=None,
) -> None:
    """Prideda vieną eilutę į kolektorių (no-op be konteksto)."""
    bucket = getattr(_state, "bucket", None)
    if bucket is None:
        return
    key = (
        supplier or "",
        str(code or "")[:120],
        kind,
        str(category_raw or "")[:500],
    )
    entry = bucket.setdefault(key, {"label": str(label or "")[:200], "count": 0, "samples": []})
    entry["count"] += 1
    if value is not None:
        sample = str(value)[:200]
        if len(entry["samples"]) < _MAX_SAMPLES and sample not in entry["samples"]:
            entry["samples"].append(sample)


def _flush(bucket: dict[tuple, dict]) -> None:
    if not bucket:
        return
    from apps.catalog.models import UnmappedAttribute
    from apps.suppliers.models import Supplier

    supplier_cache: dict[str, object] = {}
    for (supplier_code, code, kind, category_raw), entry in bucket.items():
        supplier = None
        if supplier_code:
            if supplier_code not in supplier_cache:
                supplier_cache[supplier_code] = Supplier.objects.filter(
                    code=supplier_code
                ).first()
            supplier = supplier_cache[supplier_code]
        obj = UnmappedAttribute.objects.filter(
            supplier=supplier, code=code, kind=kind, category_raw=category_raw
        ).first()
        if obj is None:
            UnmappedAttribute.objects.create(
                supplier=supplier,
                code=code,
                label=entry["label"],
                kind=kind,
                category_raw=category_raw,
                sample_values=entry["samples"],
                count=entry["count"],
            )
        else:
            obj.count = entry["count"]
            obj.sample_values = entry["samples"]
            if entry["label"]:
                obj.label = entry["label"]
            obj.save()
