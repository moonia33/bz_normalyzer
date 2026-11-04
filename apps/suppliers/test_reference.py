import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook

from apps.catalog.models import Category
from apps.ingestion.dto import NormalizedItem
from apps.ingestion.models import Offer
from apps.ingestion.pipeline import _apply_reference, _reference_map
from apps.pricing.models import PriceRule
from apps.suppliers.models import Supplier, SupplierReferenceItem


def _sheet(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "ClimaxLine"
    ws.append(["total overview"])
    ws.append(["item number", "discription", "HEK/WSP 2027", "discounted HEK/WSP",
               "UVP/RRP 2027", "EAN/Barcode", "VE / TP"])
    ws.append([100532, "Speedfädel", 6.25, 6.25, 12.95, "4048855357425", 10])
    ws.append(["KR-AC1", "Tungsten Sinkers", 2.79, 2.79, 0, 4048855408653, 10])
    ws.append(["KR-AC1", "Tungsten Sinkers", 2.79, 2.79, 5.95, "", 10])
    ws.append([0, 0, 0])
    wb.save(path)


class PricesheetTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(code="ockert", name="Ockert")
        self.with_uvp = Offer.objects.create(
            supplier=self.supplier, external_id="100532", name="Speed",
            purchase_price=Decimal("6.25"), selling_price=Decimal("6.25"),
        )
        self.no_uvp = Offer.objects.create(
            supplier=self.supplier, external_id="KR-AC1", name="Sinkers",
            purchase_price=Decimal("2.79"),
        )

    def test_import_sets_ean_and_uvp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sheet.xlsx"
            _sheet(path)
            out = StringIO()
            call_command("import_supplier_pricesheet", str(path), supplier="ockert", stdout=out)
        self.assertIn("rows=2", out.getvalue())
        ref = SupplierReferenceItem.objects.get(supplier=self.supplier, code="KR-AC1")
        self.assertEqual(ref.ean, "4048855408653")
        self.assertEqual(ref.rrp_price, Decimal("5.9500"))
        self.assertEqual(ref.trade_pack_qty, 10)
        self.with_uvp.refresh_from_db()
        self.no_uvp.refresh_from_db()
        self.assertEqual(self.with_uvp.ean, "4048855357425")
        self.assertEqual(self.with_uvp.rrp_price, Decimal("12.95"))
        self.assertEqual(self.with_uvp.selling_price, Decimal("12.95"))
        self.assertEqual(self.no_uvp.ean, "4048855408653")
        self.assertEqual(self.no_uvp.rrp_price, Decimal("5.9500"))

    def test_apply_reference_fills_only_missing(self):
        SupplierReferenceItem.objects.create(
            supplier=self.supplier, code="100532", ean="111", rrp_price=Decimal("12.95")
        )
        refs = _reference_map(self.supplier)
        item = NormalizedItem(external_id="100532", name="x")
        _apply_reference(item, refs)
        self.assertEqual(item.ean, "111")
        self.assertEqual(item.extra["rrp_price"], "12.95")
        own = NormalizedItem(external_id="100532", name="x", ean="222", extra={"rrp_price": "9.99"})
        _apply_reference(own, refs)
        self.assertEqual(own.ean, "222")
        self.assertEqual(own.extra["rrp_price"], "9.99")


class CategoryMarkupRulesTests(TestCase):
    def test_category_median_and_fallback(self):
        supplier = Supplier.objects.create(code="ockert", name="Ockert")
        lines = Category.objects.create(name="Valai", slug="valai")
        rigs = Category.objects.create(name="Sistemėlės", slug="sistemeles")
        for i, rrp in enumerate(["20.00", "22.00", "18.00"]):
            Offer.objects.create(
                supplier=supplier, external_id=f"u{i}", name="u", category=lines,
                purchase_price=Decimal("10.00"), rrp_price=Decimal(rrp),
            )
        line = Offer.objects.create(
            supplier=supplier, external_id="n1", name="n", category=lines,
            purchase_price=Decimal("10.00"),
        )
        rig = Offer.objects.create(
            supplier=supplier, external_id="n2", name="n", category=rigs,
            purchase_price=Decimal("5.00"),
        )
        call_command("category_markup_rules", supplier="ockert", apply=True, stdout=StringIO())
        self.assertEqual(PriceRule.objects.filter(supplier=supplier, only_without_rrp=True).count(), 2)
        line.refresh_from_db()
        rig.refresh_from_db()
        self.assertEqual(line.selling_price, Decimal("19.95"))
        self.assertEqual(rig.selling_price, Decimal("9.95"))
        kept = Offer.objects.get(external_id="u1")
        self.assertEqual(kept.selling_price, Decimal("22.00"))
