from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.ingestion.models import Offer
from apps.pricing.engine import compute_selling_price
from apps.suppliers.models import Supplier, SupplierReferenceItem

_EAN_RE = re.compile(r"^\d{8,14}$")


def _code(value) -> str:
    text = str(value if value is not None else "").strip().removesuffix(".0")
    return text.upper()


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        return None
    return number.quantize(Decimal("0.0001")) if number > 0 else None


def _ean(value) -> str:
    text = _code(value)
    return text if _EAN_RE.match(text) else ""


def _int(value) -> int | None:
    try:
        number = int(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number > 0 else None


def _columns(header: list[str]) -> dict[str, int]:
    cols: dict[str, int] = {}
    for idx, raw in enumerate(header):
        label = raw.strip().lower()
        if not label:
            continue
        if label == "item number":
            cols.setdefault("code", idx)
        elif label.startswith(("discription", "description")):
            cols.setdefault("name", idx)
        elif label.startswith("hek"):
            cols.setdefault("hek", idx)
        elif "uvp" in label or "rrp" in label:
            cols.setdefault("uvp", idx)
        elif "ean" in label or "barcode" in label:
            cols.setdefault("ean", idx)
        elif label.startswith("ve"):
            cols.setdefault("ve", idx)
    return cols


def _cell(values: tuple, cols: dict[str, int], key: str):
    idx = cols.get(key)
    return values[idx] if idx is not None and idx < len(values) else None


def parse_pricesheet(path: Path) -> list[dict]:
    """Ockert tipo užsakymo lapas: kiekviename lape antraštė su 'item number'."""
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=True, read_only=True)
    rows: dict[str, dict] = {}
    for sheet in workbook.worksheets:
        cols: dict[str, int] | None = None
        for values in sheet.iter_rows(values_only=True):
            if cols is None:
                header = [str(v or "") for v in values]
                if any(h.strip().lower() == "item number" for h in header):
                    cols = _columns(header)
                continue

            def cell(key: str, values=values, cols=cols):
                return _cell(values, cols, key)

            code = _code(cell("code"))
            if not code or code == "0":
                continue
            row = {
                "code": code,
                "name": str(cell("name") or "").strip()[:500],
                "ean": _ean(cell("ean")),
                "purchase_price": _decimal(cell("hek")),
                "rrp_price": _decimal(cell("uvp")),
                "trade_pack_qty": _int(cell("ve")),
                "source": f"{path.name}:{sheet.title}"[:200],
            }
            existing = rows.get(code)
            if existing is None:
                rows[code] = row
            else:
                # Pasikartojantis kodas: pirmoji eilutė lieka, trūkstami laukai papildomi.
                for key, value in row.items():
                    if value not in (None, "") and existing.get(key) in (None, ""):
                        existing[key] = value
    return list(rows.values())


class Command(BaseCommand):
    help = (
        "Įkelia tiekėjo kainoraštį / užsakymo lapą (xlsx) į SupplierReferenceItem ir "
        "esamiems pasiūlymams papildo trūkstamus EAN ir UVP (rrp_price)."
    )

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--supplier", required=True)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--no-update-offers",
            action="store_true",
            help="Tik įrašyti eilutes; pasiūlymai atsinaujins per kitą importą.",
        )

    def handle(self, *args, **opts):
        path = Path(opts["path"])
        if not path.exists():
            raise CommandError(f"Nėra failo: {path}")
        try:
            supplier = Supplier.objects.get(code=opts["supplier"])
        except Supplier.DoesNotExist as exc:
            raise CommandError(f"Nėra tiekėjo {opts['supplier']}") from exc

        rows = parse_pricesheet(path)
        by_code = {row["code"]: row for row in rows}
        stats = {"rows": len(rows), "with_ean": 0, "with_uvp": 0, "offers": 0,
                 "matched": 0, "ean_set": 0, "rrp_set": 0, "price_changed": 0}
        stats["with_ean"] = sum(1 for r in rows if r["ean"])
        stats["with_uvp"] = sum(1 for r in rows if r["rrp_price"] is not None)

        with transaction.atomic():
            for row in rows:
                SupplierReferenceItem.objects.update_or_create(
                    supplier=supplier,
                    code=row["code"],
                    defaults={k: v for k, v in row.items() if k != "code"},
                )
            if not opts["no_update_offers"]:
                offers = Offer.objects.filter(supplier=supplier).select_related("supplier", "product")
                for offer in offers:
                    stats["offers"] += 1
                    row = by_code.get(_code(offer.external_id))
                    if row is None:
                        continue
                    stats["matched"] += 1
                    fields = []
                    if not offer.ean and row["ean"]:
                        offer.ean = row["ean"]
                        fields.append("ean")
                        stats["ean_set"] += 1
                    if offer.rrp_price is None and row["rrp_price"] is not None:
                        offer.rrp_price = row["rrp_price"]
                        fields.append("rrp_price")
                        stats["rrp_set"] += 1
                    price = compute_selling_price(offer)
                    if price != offer.selling_price:
                        offer.selling_price = price
                        fields.append("selling_price")
                        stats["price_changed"] += 1
                    if fields:
                        offer.save(update_fields=[*fields, "updated_at"])
                    product = offer.product
                    if product is not None:
                        product_fields = []
                        if not product.ean and offer.ean:
                            product.ean = offer.ean
                            product_fields.append("ean")
                        if product.rrp_price is None and offer.rrp_price is not None:
                            product.rrp_price = offer.rrp_price
                            product_fields.append("rrp_price")
                        if product_fields:
                            product.save(update_fields=[*product_fields, "updated_at"])
            if opts["dry_run"]:
                transaction.set_rollback(True)

        prefix = "[dry-run] " if opts["dry_run"] else ""
        self.stdout.write(prefix + " ".join(f"{k}={v}" for k, v in stats.items()))
