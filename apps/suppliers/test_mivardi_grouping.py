from unittest import mock

from django.test import SimpleTestCase

from apps.suppliers.adapters import mivardi


def _item(code, name, category, params=()):
    ps = "".join(f'<PARAM name="{k}">{v}</PARAM>' for k, v in params)
    return (
        f"<SHOPITEM><PRODUCTNO>{code}</PRODUCTNO><PRODUCT>{name}</PRODUCT>"
        f"<CATEGORYTEXT>{category}</CATEGORYTEXT><PARAMETERS>{ps}</PARAMETERS></SHOPITEM>"
    )


def _parse(*items):
    feed = mock.Mock(file=None, url="https://example.test/feed", config={})
    xml = ("<SHOP>" + "".join(items) + "</SHOP>").encode()
    return {r["code"]: r for r in mivardi.MivardiAdapter(feed).parse(xml)}


class MivardiGroupingTests(SimpleTestCase):
    def test_apparel_sizes_grouped_with_size_axis(self):
        rows = _parse(
            _item("TS", "T-shirt MC Team - S", "Oblečení a obuv | Trička", [("Velikost", "S")]),
            _item("TXL", "T-shirt MC Team - XL", "Oblečení a obuv | Trička", [("Velikost", "XL")]),
            _item("H3", "Hoody TM Black - 3XL", "Oblečení a obuv | Mikiny"),
            _item("HM", "Hoody TM Black - M", "Oblečení a obuv | Mikiny"),
        )
        self.assertEqual(rows["TS"]["product_group"], rows["TXL"]["product_group"])
        self.assertTrue(rows["TS"]["product_group"])
        self.assertEqual(rows["H3"]["product_group"], rows["HM"]["product_group"])
        self.assertEqual((rows["H3"]["axes"]["size"], rows["HM"]["axes"]["size"]), ("3XL", "M"))

    def test_hook_numbers_grouped_and_variant_kept_apart(self):
        cat = "Háčky | Háčky Yamatsu"
        rows = _parse(
            _item("C4", "Yamatsu Chinu 4", cat),
            _item("C10", "Yamatsu Chinu 1/0", cat),
            _item("F4", "Yamatsu Chinu 4 - flatted", cat),
            _item("F2", "Yamatsu Chinu 2 - flatted", cat),
        )
        self.assertEqual(rows["C4"]["product_group"], rows["C10"]["product_group"])
        self.assertEqual(rows["F4"]["product_group"], rows["F2"]["product_group"])
        self.assertNotEqual(rows["C4"]["product_group"], rows["F4"]["product_group"])
        self.assertEqual(rows["C10"]["axes"]["hook_size"], "1/0")

    def test_line_diameters_grouped(self):
        cat = "Vlasce a šňůry | Vlasce - Kaprařina"
        rows = _parse(
            _item("R1", "Royal Carp 0,225 mm 300 m", cat, [("Průměr", "0,225 mm")]),
            _item("R2", "Royal Carp 0,255 mm 300 m", cat, [("Průměr", "0,255 mm")]),
        )
        self.assertEqual(rows["R1"]["product_group"], rows["R2"]["product_group"])
        self.assertTrue(rows["R1"]["product_group"])

    def test_size_letter_not_stripped_outside_apparel(self):
        rows = _parse(
            _item("A", "Rod rest MC1 - S", "Stojany | Vidličky"),
            _item("B", "Shelter Quick Set XL", "Bivaky | Přístřešky"),
        )
        self.assertEqual(rows["A"]["product_group"], "")
        self.assertEqual(rows["B"]["base"], "Shelter Quick Set XL")

    def test_name_tail_does_not_overwrite_param_axis(self):
        cat = "Dravec | Lanka"
        rows = _parse(
            _item("W12", "Wire leaders (swivel + treble hook) 12 kg", cat, [("Hmotnost", "0,13 kg")]),
            _item("W9", "Wire leaders (swivel + treble hook) 9 kg", cat, [("Hmotnost", "0,13 kg")]),
        )
        self.assertEqual(rows["W12"]["product_group"], rows["W9"]["product_group"])
        self.assertEqual(rows["W12"]["axes"]["weight"], rows["W9"]["axes"]["weight"])
        self.assertEqual((rows["W12"]["axes"]["model"], rows["W9"]["axes"]["model"]), ("12 kg", "9 kg"))
