import io
from decimal import Decimal
from typing import ClassVar

from django.test import SimpleTestCase
from openpyxl import Workbook

from apps.suppliers.adapters.albatros import (
    AlbatrosAdapter,
    badges_from_mark,
    detect_brand,
    normalize_code,
)
from apps.suppliers.adapters.base import AdapterError


class _Feed:
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
