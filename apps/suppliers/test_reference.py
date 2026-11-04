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
from apps.ingestion.pipeline import (
    _apply_reference,
    _left_split_group,
    _reference_map,
    _reference_variants,
)
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


    def test_apply_reference_fills_missing_axes(self):
        SupplierReferenceItem.objects.create(
            supplier=self.supplier, code="9391-03000-040",
            name="3000m 0,40mm 38kg CULT Catfish X-treme Braid",
        )
        SupplierReferenceItem.objects.create(
            supplier=self.supplier, code="9910-00021-030", name="30g CULT Fireball Montage natural,SB",
        )
        SupplierReferenceItem.objects.create(
            supplier=self.supplier, code="8351-10015-026", name="5x15m 0,26-0,58mm Seamaster Surf Line SB",
        )
        refs = _reference_map(self.supplier)
        braid = NormalizedItem(external_id="9391-03000-040", name="x", attributes={"axes": {"model": "Braid"}})
        _apply_reference(braid, refs)
        self.assertEqual(
            braid.attributes["axes"],
            {"model": "Braid", "line_length": "3000m", "line_diameter": "0.40mm", "line_strength": "38kg"},
        )
        rig = NormalizedItem(external_id="9910-00021-030", name="x")
        _apply_reference(rig, refs)
        self.assertEqual(rig.attributes["axes"], {"weight": "30g"})
        surf = NormalizedItem(external_id="8351-10015-026", name="x", attributes={"axes": {"line_length": "15m"}})
        _apply_reference(surf, refs)
        self.assertEqual(surf.attributes["axes"], {"line_length": "15m", "line_diameter": "0.26-0.58mm"})
        own = NormalizedItem(
            external_id="9391-03000-040", name="x",
            attributes={"axes": {"line_length": "280m", "line_diameter": "0.35mm", "line_strength": "30kg"}},
        )
        _apply_reference(own, refs)
        self.assertEqual(own.attributes["axes"]["line_length"], "280m")


    def test_reference_names_split_identical_variants(self):
        for code, name in (
            ("KR-AC24", "Line Aligner / short brown, 10pc"),
            ("KR-AC25", "Line Aligner / short weed, 10pc"),
            ("KR-CL1301", "T-Shirt schwarz mit Logo M"),
            ("KR-CL1302", "T-Shirt schwarz mit  Logo L"),
            ("KR-AC6", "Hook Silicone Tubing brown"),
            ("KR-AC9", "Hook Silicone Tubing brown"),
        ):
            SupplierReferenceItem.objects.create(supplier=self.supplier, code=code, name=name)
        refs = _reference_map(self.supplier)

        def item(code, group, axes):
            return NormalizedItem(external_id=code, name="x", attributes={"axes": dict(axes)},
                                  extra={"product_group": group})

        items = [
            item("KR-AC24", "aligner", {}), item("KR-AC25", "aligner", {}),
            item("KR-CL1301", "tee", {"color": "Schwarz"}), item("KR-CL1302", "tee", {"color": "Schwarz"}),
            item("KR-AC6", "tubing", {}), item("KR-AC9", "tubing", {}),
        ]
        axes = {i.external_id: i.attributes.get("axes") for i in _reference_variants(items, refs)}
        self.assertEqual(axes["KR-AC24"], {"model": "brown"})
        self.assertEqual(axes["KR-AC25"], {"model": "weed"})
        self.assertEqual(axes["KR-CL1301"], {"color": "Schwarz", "size": "M"})
        self.assertEqual(axes["KR-CL1302"], {"color": "Schwarz", "size": "L"})
        self.assertEqual(axes["KR-AC6"], {})


    def test_split_offer_leaves_old_page_product(self):
        from types import SimpleNamespace

        old = SimpleNamespace(product_group="seamaster-leader")
        split = NormalizedItem(external_id="8390-1", name="x",
                               extra={"split_from_group": "seamaster-leader"})
        fc = SimpleNamespace(product_group="seamaster-leader-seamaster-fluorocarbon-leader")
        self.assertTrue(_left_split_group(old, split, fc))
        new = SimpleNamespace(product_group=fc.product_group)
        self.assertFalse(_left_split_group(new, split, fc))
        plain = NormalizedItem(external_id="8382-1", name="x", extra={})
        self.assertFalse(_left_split_group(old, plain, SimpleNamespace(product_group="other")))


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
