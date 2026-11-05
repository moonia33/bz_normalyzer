import io
from decimal import Decimal
from typing import ClassVar

from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook

from apps.suppliers.adapters.albatros import (
    AlbatrosAdapter,
    badges_from_mark,
    detect_brand,
    normalize_code,
)
from apps.suppliers.adapters.base import AdapterError


class _Feed:
    config: ClassVar[dict] = {"manufacturer_groups": False}


class _DbFeed:
    config: ClassVar[dict] = {}


def _xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


HEADER = [None, "Code", "Item", "be PVM, export", "SALE", "Q-ty", "Order", None, "FOTO"]


class AlbatrosAdapterTests(SimpleTestCase):
    def setUp(self):
        self.adapter = AlbatrosAdapter(_Feed())

    def _items(self, rows):
        return [self.adapter.normalize(r) for r in self.adapter.parse(_xlsx([HEADER, *rows]))]

    def test_parse_sections_prices_badges(self):
        items = self._items([
            ["Reel - Ritė"],
            ["", 143792, "TiCA Lustre LCAT3000  1+1bb", 10, -0.3, 3, None, "http://albatros.lt/public/1.jpg", "FOTO"],
            ["New 2027", "10005-100", "Savage Gear Revenge SG4", 20, None, 0],
            ["Bait / RAPALA"],
            ["", 143792.0, "TiCA Lustre LCAT3000 dup", 12, None, 1],
            ["", 555, "VOBLERIS RAPALA BXB-3 BGH", None, None, 2],
        ])
        by = {i.external_id: i for i in items}
        self.assertEqual(sorted(by), ["10005-100", "143792", "555"])
        a = self._items([["Reel - Ritė"], ["", 1, "TiCA X", 10, -0.3, 3, None, "http://x/1.jpg"]])[0]
        self.assertEqual(a.purchase_price, Decimal("7.00"))
        self.assertEqual(a.extra["rrp_price"], "15.00")
        self.assertEqual(a.discount_percent, 30)
        self.assertEqual(a.category_path, "Reel - Ritė")
        self.assertEqual(a.images, ["http://x/1.jpg"])
        self.assertEqual(a.badges, [])
        self.assertEqual(a.brand_name, "TiCA")
        new = by["10005-100"]
        self.assertEqual(new.badges, ["new-2027"])
        self.assertIsNone(new.discount_percent)
        self.assertEqual(new.purchase_price, Decimal("20.00"))
        self.assertEqual(new.stock, 0)
        self.assertFalse(new.is_available)
        # dubliuotas kodas – paskutinė eilutė
        self.assertEqual(by["143792"].name, "TiCA Lustre LCAT3000 dup")
        self.assertEqual(by["143792"].category_path, "Bait / RAPALA")
        self.assertIsNone(by["555"].purchase_price)
        self.assertEqual(by["555"].brand_name, "Rapala")

    def test_unrecognised_file_raises(self):
        with self.assertRaises(AdapterError):
            list(self.adapter.parse(_xlsx([["Code", "Item", "Price"], [1, "x", 2]])))

    def test_sale_out_of_range_raises(self):
        with self.assertRaises(AdapterError):
            self._items([["", 1, "X", 10, 0.3, 1]])

    def test_helpers(self):
        self.assertEqual(normalize_code(143792.0), "143792")
        self.assertEqual(normalize_code(" 10005-100 "), "10005-100")
        self.assertEqual(badges_from_mark("New 2027"), ["new-2027"])
        self.assertEqual(badges_from_mark(""), [])
        self.assertEqual(detect_brand("PL XLNT BIG FISH FEEDER"), "Prologic")
        self.assertEqual(detect_brand("SG 4D Line Thru Trout"), "Savage Gear")
        self.assertEqual(detect_brand("Tip part Crazy Fish Optimus"), "Crazy Fish")
        self.assertEqual(detect_brand("Lucky Craft LC FLAT"), "Lucky Craft")
        self.assertEqual(detect_brand("PROPELERIS MKP-2", "Minn Kota Accessories"), "Minn Kota")
        self.assertEqual(detect_brand("Unknown thing", "Reel - Ritė"), "")

    def test_bad_qty_raises(self):
        for qty in (None, "n/a"):
            with self.assertRaises(AdapterError):
                self._items([["", 1, "X", 10, None, qty]])

    def test_item_without_code_is_row_error(self):
        rows = list(self.adapter.parse(_xlsx([HEADER, ["", None, "No code", 10, None, 1]])))
        self.assertEqual(len(rows), 1)
        with self.assertRaises(AdapterError):
            self.adapter.normalize(rows[0])

    def test_negative_export_raises(self):
        with self.assertRaises(AdapterError):
            self._items([["", 1, "X", -5, None, 1]])

    def test_iter_items_skips_bad_rows_and_counts_errors(self):
        payload = _xlsx([
            HEADER,
            ["", 1, "Good A", 10, None, 1],
            ["", None, "No code", 10, None, 1],
            ["", 2, "Bad qty", 10, None, "n/a"],
            ["", 3, "Negative", -5, None, 1],
            ["", 4, "Good B", 10, None, 2],
        ])
        items = list(self.adapter.iter_items(payload))
        self.assertEqual([i.external_id for i in items], ["1", "4"])
        self.assertEqual(self.adapter.fetch_errors, 3)


class AlbatrosManufacturerGroupTests(TestCase):
    def setUp(self):
        from apps.suppliers.models import ManufacturerContent

        ManufacturerContent.objects.create(
            source="dam", url="https://www.dam.de/products/effzett-spoon", language="en",
            series="DAM Effzett Spoon", codes=["65045", "65046"],
            images=["https://cdn/m1.jpg"],
            specs={"sections": [{"heading": "", "text": "", "rows": {
                "65045": {"name": "x", "Weight": "10g"}, "65046": {"Weight": "20g"}}}],
                "eans": {"65045": "5706301650456"}},
        )
        ManufacturerContent.objects.create(
            source="dam", url="https://www.sportfishtackle.de/de/artiklar/dam-spoon.html",
            language="de", series="DE", codes=["65045", "65046", "65047"],
        )

    def test_groups_by_manufacturer_page(self):
        rows = [
            ["New 2027", "65045", "DAM EFFZETT SPOON 10G", 2, None, 3, None, None, "https://s/f.jpg"],
            [None, "65046", "DAM EFFZETT SPOON 20G", 2, None, 0, None, None, None],
            [None, "65047", "DAM EFFZETT SPOON 30G", 2, None, 0, None, None, None],
            [None, "99999", "DAM OTHER", 2, None, 0, None, None, "https://s/o.jpg"],
        ]
        items = {i.external_id: i for i in AlbatrosAdapter(_DbFeed()).iter_items(_xlsx([HEADER, ["Bait"], *rows]))}
        a, b, c, other = items["65045"], items["65046"], items["65047"], items["99999"]
        self.assertEqual(a.extra["product_group"], "dam:effzett-spoon")
        self.assertEqual(b.extra["product_group"], "dam:effzett-spoon")
        self.assertTrue(a.extra["strict_group"])
        self.assertEqual(a.extra["base_name"], "DAM Effzett Spoon")
        self.assertEqual(a.attributes["axes"], {"Weight": "10g"})
        self.assertEqual(a.ean, "5706301650456")
        self.assertEqual(a.images, ["https://s/f.jpg", "https://cdn/m1.jpg"])
        self.assertEqual(b.extra["main_image_url"], "https://cdn/m1.jpg")
        self.assertEqual(c.extra["product_group"], "dam:dam-spoon")
        self.assertEqual(c.attributes, {})
        self.assertNotIn("product_group", other.extra)
        self.assertEqual(other.name, "DAM OTHER")
        self.assertEqual(other.images, ["https://s/o.jpg"])

    def test_twins_without_axes_get_model_from_stock_name(self):
        from apps.suppliers.models import ManufacturerContent

        ManufacturerContent.objects.create(
            source="savage-gear", url="https://x/de/artiklar/sg-exorus.html", language="de",
            codes=["1609503", "1609504"],
        )
        rows = [
            [None, "1609503", "Savage Gear Exorus 1000 7bb 5.2:1 210g", 2, None, 1, None, None, None],
            [None, "1609504", "Savage Gear Exorus 2500 7bb 5.2:1 260g", 2, None, 1, None, None, None],
        ]
        items = list(AlbatrosAdapter(_DbFeed()).iter_items(_xlsx([HEADER, ["Reel"], *rows])))
        self.assertEqual([i.attributes["axes"] for i in items], [{"model": "1000 210g"}, {"model": "2500 260g"}])
