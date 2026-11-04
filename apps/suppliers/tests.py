from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase

from apps.suppliers.adapters.base import BaseAdapter
from apps.suppliers.adapters.generic_excel import GenericExcelAdapter
from apps.suppliers.adapters.moss import MossAdapter
from apps.suppliers.adapters.registry import get_adapter, list_adapters


class AdapterRegistryTests(TestCase):
    def test_generic_adapters_registered(self):
        codes = set(list_adapters())
        self.assertIn("generic_xml", codes)
        self.assertIn("generic_json", codes)
        self.assertIn("generic_excel", codes)
        self.assertIn("fox", codes)
        self.assertIn("fox_stock", codes)
        self.assertIn("fox_discontinued", codes)
        self.assertIn("moss", codes)
        self.assertIsNotNone(get_adapter("generic_json"))
        self.assertIsNotNone(get_adapter("fox"))
        self.assertIsNotNone(get_adapter("fox_stock"))
        self.assertIsNotNone(get_adapter("fox_discontinued"))
        self.assertIsNotNone(get_adapter("moss"))


class AdapterNormalizationTests(TestCase):
    def test_decimal_parsing_handles_european_and_currency_strings(self):
        self.assertEqual(BaseAdapter._to_decimal("1.234,56"), Decimal("1234.56"))
        self.assertEqual(BaseAdapter._to_decimal("€ 1,234.56"), Decimal("1234.56"))
        self.assertEqual(BaseAdapter._to_decimal("(99,95)"), Decimal("-99.95"))
        self.assertEqual(BaseAdapter._to_int("1,234"), 1234)

    def test_csv_parser_detects_semicolon_delimiter(self):
        payload = b"id;name;price\n1;Widget;1.234,56\n"
        adapter = GenericExcelAdapter.__new__(GenericExcelAdapter)
        adapter.config = {}
        adapter.feed = type("Feed", (), {"file": None})()
        rows = list(adapter._parse_csv(payload))

        self.assertEqual(rows[0]["price"], "1.234,56")
        self.assertEqual(rows[0]["name"], "Widget")


class OckertAdapterTests(TestCase):
    PAYLOAD = {
        "items": [
            {
                "art_no": "108293",
                "descriptor": "SK2912",
                "hek": "139.99",
                "uvp": "219.95",
                "availability": "green",
                "stock_text": "Verfügbar",
                "ve": "1",
                "table_title": "Laks RS-2",
                "specs": {"l (cm)": "292", "l (ft)": "9.5", "wg (gr)": "35", "teile": "2"},
                "product_url": "/Produkt/Laks-RS-2-16916082",
                "product_name": "Laks RS-2",
                "category_path": ["Sportex Ruten", "Salmon Ruten", "Laks Serie"],
                "image": "https://shop.ockert.net/cust/images/_thumbs/_11/laks.jpg",
            },
            {
                "art_no": "KR-RR1",
                "descriptor": "20lb 20cm Synx Ronnie Rig #2, 2pc",
                "hek": "2.75",
                "availability": "yellow",
                "stock_text": "Wenig verfügbar",
                "ve": "10",
                "table_title": "Ronnie Rig",
                "specs": {},
                "product_url": "/Produkt/Kryston-Rigs-9328737",
                "product_name": "Kryston Rigs",
                "category_path": ["Kryston", "Rigs"],
                "image": "",
            },
            {
                "art_no": "8756-10025-010",
                "hek": "3.99",
                "availability": "red",
                "ve": "10",
                "table_title": "25m",
                "specs": {"durchm. ø in mm": "0.10", "tragkraft in kg": "1.2"},
                "product_url": "/Produkt/Max-Fluorocarbon--18655892",
                "product_name": "Max Fluorocarbon",
                "category_path": ["Angelschnüre", "Fluorocarbon & HardMono"],
                "image": "",
            },
        ]
    }

    def _items(self):
        import json

        from apps.suppliers.adapters.ockert import OckertAdapter

        adapter = OckertAdapter(
            SimpleNamespace(
                config={
                    "brand_map": {"Sportex": "Sportex", "TICA": "TICA", "Kryston": "Kryston"},
                    "default_brand": "Climax",
                }
            )
        )
        payload = json.dumps(self.PAYLOAD).encode()
        return {i.external_id: i for i in map(adapter.normalize, adapter.parse(payload))}

    def test_rod_spec_columns_to_axes(self):
        items = self._items()
        rod = items["108293"]
        self.assertEqual(
            rod.attributes["axes"],
            {
                "rod_length_m": "2.92m",
                "rod_length_ft": "9.5",
                "casting_weight": "35g",
                "sections": "2",
            },
        )
        self.assertEqual(rod.name, "Laks RS-2 SK2912")
        self.assertEqual(rod.extra["product_group"], "laks-rs-2")
        self.assertEqual(rod.brand_name, "Sportex")
        self.assertEqual(str(rod.extra["rrp_price"]), "219.95")

    def test_outlet_rods_get_type_from_name(self):
        from apps.suppliers.adapters.ockert import OckertAdapter

        adapter = OckertAdapter(SimpleNamespace(config={"default_brand": "Climax"}))
        base = dict(self.PAYLOAD["items"][0], category_path=["Sportex Ruten", "Auslauf Ruten"])
        cases = {
            "Revolt Carp Stalker": "Karpfenruten",
            "Top Cat Wallerruten": "Wallerruten",
            "Synoris Fly": "Fliegenruten",
            "Magnus Mastergrade Meeresruten": "Meeresruten",
            "JIG-Xpert": "Spinnruten",
        }
        for product_name, rod_type in cases.items():
            item = adapter.normalize(dict(base, product_name=product_name, table_title=product_name))
            self.assertEqual(item.category_path, f"Sportex Ruten > Auslauf Ruten > {rod_type}")
        item = adapter.normalize(self.PAYLOAD["items"][0])
        self.assertEqual(item.category_path, "Sportex Ruten > Salmon Ruten > Laks Serie")

    def test_thousands_separator_line_length(self):
        from apps.suppliers.adapters.ockert import OckertAdapter, _descriptor_axes

        adapter = OckertAdapter(SimpleNamespace(config={"default_brand": "Climax"}))
        row = dict(
            self.PAYLOAD["items"][0],
            product_name="X8 multicolor",
            table_title="X8 multicolor 3.500m",
            specs={},
        )
        self.assertEqual(adapter.normalize(row).attributes["axes"]["line_length"], "3500m")
        row["table_title"] = "X8 multicolor 350m"
        self.assertEqual(adapter.normalize(row).attributes["axes"]["line_length"], "350m")
        axes: dict = {}
        _descriptor_axes("Cult Carp 1.000m 0,30mm", axes)
        self.assertEqual(axes["line_length"], "1000m")

    def test_pack_and_availability(self):
        items = self._items()
        # VE>1 -> trade_pack_qty (B2B), bet parduodama po 1 vnt.
        self.assertEqual(items["KR-RR1"].extra["trade_pack_qty"], 10)
        self.assertNotIn("sold_as_pack", items["KR-RR1"].extra)
        self.assertNotIn("sold_as_pack", items["108293"].extra)
        # yellow = 10 (leidžia į krepšį), green = 20, red = 0.
        self.assertTrue(items["KR-RR1"].is_available)
        self.assertEqual(items["KR-RR1"].stock, 10)
        self.assertTrue(items["108293"].is_available)
        self.assertEqual(items["108293"].stock, 20)
        self.assertFalse(items["8756-10025-010"].is_available)
        self.assertEqual(items["8756-10025-010"].stock, 0)

    def test_descriptor_axes_and_line_specs(self):
        items = self._items()
        rig = items["KR-RR1"]
        self.assertEqual(rig.attributes["axes"]["line_strength"], "20lb")
        self.assertEqual(rig.attributes["axes"]["hook_size"], "2")
        self.assertEqual(rig.attributes["axes"]["model"], "Ronnie Rig")
        line = items["8756-10025-010"]
        self.assertEqual(line.attributes["axes"]["line_diameter"], "0.10mm")
        self.assertEqual(line.attributes["axes"]["line_strength"], "1.2kg")
        self.assertEqual(line.brand_name, "Climax")


class MossAccessoryTests(TestCase):
    FEED = b"""<SHOP>
<SHOPITEM><PRODUCT_ID>922</PRODUCT_ID><PRODUCT>Feeder set Delphin YAKUZA 300</PRODUCT>
<IS_ACCESSORY>false</IS_ACCESSORY><DESCRIPTION>Complete feeder set</DESCRIPTION>
<PRICE_VAT>52,94</PRICE_VAT><AVAILABLE>5</AVAILABLE></SHOPITEM>
<SHOPITEM><PRODUCT_ID>981</PRODUCT_ID><VARIANT>922</VARIANT><PRODUCT>Tip Delphin Yakuza Feeder</PRODUCT>
<IS_ACCESSORY>true</IS_ACCESSORY><DESCRIPTION>Complete feeder set</DESCRIPTION>
<PRICE_VAT>4,79</PRICE_VAT><AVAILABLE>3</AVAILABLE></SHOPITEM>
<SHOPITEM><PRODUCT_ID>982</PRODUCT_ID><VARIANT>922</VARIANT><PRODUCT>Spare spool</PRODUCT>
<IS_ACCESSORY>true</IS_ACCESSORY><DESCRIPTION>Own spool text</DESCRIPTION>
<PRICE_VAT>8,10</PRICE_VAT><AVAILABLE>3</AVAILABLE></SHOPITEM>
</SHOP>"""

    def test_accessory_drops_main_product_description(self):
        adapter = MossAdapter(SimpleNamespace(config={}))
        items = {i.external_id: i for i in map(adapter.normalize, adapter.parse(self.FEED))}
        self.assertEqual(items["922"].description, "Complete feeder set")
        self.assertFalse(items["922"].extra["is_accessory"])
        self.assertEqual(items["981"].description, "")
        self.assertTrue(items["981"].extra["is_accessory"])
        self.assertEqual(items["981"].extra["description_cleared"], "Complete feeder set")
        self.assertEqual(items["982"].extra["description_cleared"], "")
        self.assertEqual(items["982"].description, "Own spool text")
