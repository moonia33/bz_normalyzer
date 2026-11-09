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

    def test_ean_shared_by_different_codes_is_dropped(self):
        from apps.suppliers.models import ManufacturerContent

        ManufacturerContent.objects.create(
            source="dam", url="https://x/de/artiklar/dam-other.html", language="de",
            codes=["99999"], specs={"eans": {"99999": "5706301650456"}},
        )
        rows = [
            [None, "65045", "DAM EFFZETT SPOON 10G", 2, None, 3, None, None, None],
            [None, "99999", "DAM OTHER", 2, None, 0, None, None, None],
        ]
        items = list(AlbatrosAdapter(_DbFeed()).iter_items(_xlsx([HEADER, ["Bait"], *rows])))
        self.assertEqual([i.ean for i in items], [None, None])

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


class AlbatrosPhotoAndFixedGroupTests(SimpleTestCase):
    def _items(self, rows, config=None):
        class Feed:
            pass

        Feed.config = {"manufacturer_groups": False, **(config or {})}
        return {i.external_id: i for i in AlbatrosAdapter(Feed()).iter_items(_xlsx([HEADER, ["Line"], *rows]))}

    ROWS = (
        [None, "77637", "DAM Damyl Tectan Hyper 4Oz 1.200M 0.30mm", 2, None, 1, None, None, "https://s/t.jpg"],
        [None, "77638", "DAM Damyl Tectan Hyper 4Oz 860M 0.35mm", 2, None, 1, None, None, "https://s/t.jpg"],
        [None, "11111", "DAM Spoon 10g", 2, None, 1, None, None, "https://s/g.jpg"],
        [None, "22222", "DAM Feeder 20g", 2, None, 1, None, None, "https://s/g.jpg"],
    )

    def test_same_photo_brand_section_groups_with_name_prefix(self):
        items = self._items(self.ROWS)
        a, b = items["77637"], items["77638"]
        self.assertEqual(a.extra["product_group"], b.extra["product_group"])
        self.assertTrue(a.extra["product_group"].startswith("dam:photo:"))
        self.assertEqual(a.extra["base_name"], "DAM Damyl Tectan Hyper 4Oz")
        self.assertEqual(a.attributes["axes"], {"model": "1.200M 0.30mm"})
        self.assertEqual(b.attributes["axes"], {"model": "860M 0.35mm"})

    def test_generic_photo_with_only_brand_in_common_is_not_grouped(self):
        items = self._items(self.ROWS)
        self.assertNotIn("product_group", items["11111"].extra)
        self.assertNotIn("product_group", items["22222"].extra)

    def test_same_photo_in_other_section_is_other_group(self):
        rows = [
            *self.ROWS[:2],
            ["Reels"],
            [None, "77639", "DAM Damyl Tectan Hyper 4Oz 500M 0.30mm", 2, None, 1, None, None, "https://s/t.jpg"],
            [None, "77640", "DAM Damyl Tectan Hyper 4Oz 600M 0.35mm", 2, None, 1, None, None, "https://s/t.jpg"],
        ]
        items = self._items(rows)
        self.assertNotEqual(items["77637"].extra["product_group"], items["77639"].extra["product_group"])
        self.assertEqual(items["77639"].extra["product_group"], items["77640"].extra["product_group"])

    def test_photo_groups_can_be_disabled(self):
        items = self._items(self.ROWS, {"photo_groups": False})
        self.assertNotIn("product_group", items["77637"].extra)

    def test_fixed_groups_override(self):
        items = self._items(
            self.ROWS,
            {"fixed_groups": {"77637": "ps:42", "11111": {"group": "ps:42", "base_name": "PS name"}}},
        )
        self.assertEqual(items["77637"].extra["product_group"], "ps:42")
        self.assertEqual(items["11111"].extra["product_group"], "ps:42")
        self.assertEqual(items["11111"].extra["base_name"], "PS name")
        self.assertTrue(items["77638"].extra["product_group"].startswith("dam:photo:"))


class AlbatrosRetailFallbackTests(TestCase):
    def test_zuklys_adds_images_and_specs_without_group(self):
        from apps.suppliers.models import ManufacturerContent

        ManufacturerContent.objects.create(
            source="zuklys", url="https://zuklys.lt/49-40-lt.html", language="lt",
            series="Guminukai Crazy Fish NIMBLE", codes=["49-40-30D-6"],
            images=["https://zuklys.lt/image/cache/catalog/n-800x800.jpg"],
            specs={"sections": [{"heading": "", "text": "", "rows": {"49-40-30D-6": {"Spalva": "30d"}}}]},
        )
        rows = [[None, "49-40-30d-6", "Crazy Fish Nimble 1.6\" 1g 49-40-30d-6", 2, None, 1, None, None, None]]
        (item,) = AlbatrosAdapter(_DbFeed()).iter_items(_xlsx([HEADER, ["Soft"], *rows]))
        self.assertNotIn("product_group", item.extra)
        self.assertEqual(item.attributes, {"specs": {"Spalva": "30d"}})
        self.assertEqual(item.extra["main_image_url"], "https://zuklys.lt/image/cache/catalog/n-800x800.jpg")
        self.assertEqual(item.extra["retail_url"], "https://zuklys.lt/49-40-lt.html")

    def test_nemus_preferred_over_zuklys(self):
        from apps.suppliers.models import ManufacturerContent

        for source, url in (("zuklys", "https://zuklys.lt/j-lt.html"), ("nemus", "https://nemus.lt/j")):
            ManufacturerContent.objects.create(
                source=source, url=url, language="lt", series="Jobe", codes=["244823001-S"],
                images=[f"{url}.jpg"],
            )
        rows = [[None, "244823001-S", "Jobe 4 Buckle Life Vest S", 2, None, 1, None, None, None]]
        (item,) = AlbatrosAdapter(_DbFeed()).iter_items(_xlsx([HEADER, ["Vests"], *rows]))
        self.assertEqual(item.extra["retail_url"], "https://nemus.lt/j")
        self.assertEqual(item.images, ["https://nemus.lt/j.jpg"])
