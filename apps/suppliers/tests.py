from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

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

    def test_mixed_page_tables_split_into_products(self):
        import json

        from apps.suppliers.adapters.ockert import OckertAdapter

        def row(art, h2, url, name, path):
            return {"art_no": art, "table_title": h2, "specs": {}, "product_url": url,
                    "product_name": name, "category_path": path}

        lines = ["Angelschnüre", "Seamaster"]
        payload = {"items": [
            row("8381-1", "Seamaster Extreme Leader", "/Produkt/Seamaster-Leader-9222645", "Seamaster Leader", lines),
            row("8382-1", "Seamaster Leader", "/Produkt/Seamaster-Leader-9222645", "Seamaster Leader", lines),
            row("8390-1", "Seamaster Fluorocarbon Leader", "/Produkt/Seamaster-Leader-9222645", "Seamaster Leader", lines),
            row("9687-1", "Hard Mono", "/Produkt/Cult-Catfish-Leader-1", "Cult Catfish Leader", lines),
            row("9687-2", "Kevlar Leader", "/Produkt/Cult-Catfish-Leader-1", "Cult Catfish Leader", lines),
            row("9151-1", "Weiß", "/Produkt/Cult-Catfish-Strong-9327471", "Cult Catfish Strong", lines),
            row("9151-2", "Braun", "/Produkt/Cult-Catfish-Strong-9327471", "Cult Catfish Strong", lines),
            row("8785-1", "3.000m", "/Produkt/X8-multicolor-1", "X8 multicolor", lines),
            row("8785-2", "350m", "/Produkt/X8-multicolor-1", "X8 multicolor", lines),
            row("9794-1", "1/4 lbs", "/Produkt/Deep-Purple-1", "Cult Carp Deep Purple", lines),
            row("9794-2", "3.000m", "/Produkt/Deep-Purple-1", "Cult Carp Deep Purple", lines),
            row("9802-1", "Silt", "/Produkt/Cult-Lead-Core-1", "Cult Lead Core", lines),
            row("9802-2", "Weed", "/Produkt/Cult-Lead-Core-1", "Cult Lead Core", lines),
            row("KR-AC1", "Tungsten Sinkers", "/Produkt/Terminal-Tackle-1", "Terminal Tackle", ["Kryston", "Terminal Tackle"]),
            row("KR-AC24", "Line Aligner", "/Produkt/Terminal-Tackle-1", "Terminal Tackle", ["Kryston", "Terminal Tackle"]),
            row("159182", "JIG-Xpert Zander", "/Produkt/JIG-Xpert-1", "JIG-Xpert", ["Sportex Ruten", "Auslauf Ruten"]),
            row("159190", "JIG-Xpert Barsch", "/Produkt/JIG-Xpert-1", "JIG-Xpert", ["Sportex Ruten", "Auslauf Ruten"]),
        ]}
        adapter = OckertAdapter(SimpleNamespace(config={"brand_map": {"Kryston": "Kryston"}, "default_brand": "Climax"}))
        items = {i.external_id: i for i in map(adapter.normalize, adapter.parse(json.dumps(payload).encode()))}

        def group(art):
            return items[art].extra["product_group"], items[art].extra["base_name"]

        self.assertEqual(group("8382-1"), ("seamaster-leader", "Climax Seamaster Leader"))
        self.assertEqual(group("8381-1"), ("seamaster-leader-seamaster-extreme-leader", "Climax Seamaster Extreme Leader"))
        self.assertEqual(group("8390-1")[1], "Climax Seamaster Fluorocarbon Leader")
        self.assertEqual(group("9687-1")[1], "Climax Cult Catfish Leader Hard Mono")
        self.assertEqual(group("KR-AC24")[1], "Kryston Line Aligner")
        for a, b in (("9151-1", "9151-2"), ("8785-1", "8785-2"), ("9794-1", "9794-2"),
                     ("9802-1", "9802-2"), ("159182", "159190")):
            self.assertEqual(group(a), group(b))
        self.assertNotIn("model", items["8381-1"].attributes.get("axes", {}))

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


class AlbatrosRefineCategoriesTests(SimpleTestCase):
    def test_mixed_section_split_by_name(self):
        from apps.ingestion.dto import NormalizedItem
        from apps.suppliers.adapters.albatros import refine_categories

        def item(name, path):
            return NormalizedItem(external_id=name, sku=name, name=name, category_path=path)

        section = "CAT Fishing - Šamų žvejyba"
        items = [
            item("MADCAT Spin Tail 35g 10cm T2 Roach", section),
            item("MADCAT Power Leader 15M 1.30MM", section),
            item("MADCAT Black Spin 270 9FT/2.70M 40-150G 2SEC", section),
            item("Mepps SYCLOPS Original Gold 8g size 0", "Bait / MEPPS"),
            item("Lucky Craft Pointer 78DD", "Bait / Wobbler"),
        ]
        refine_categories(items)
        self.assertEqual(
            [i.category_path for i in items],
            [
                section,
                f"{section} > Valai",
                f"{section} > Meškerykočiai",
                "Bait / MEPPS > Blizgės",
                "Bait / Wobbler",
            ],
        )

    def test_group_shares_subcategory_from_variant_names(self):
        from apps.ingestion.dto import NormalizedItem
        from apps.suppliers.adapters.albatros import refine_categories

        section = "CAT Fishing - Šamų žvejyba"
        items = [
            NormalizedItem(
                external_id=f"R-{n}", sku=f"R-{n}", name=f"MADCAT Black Spin {n}", category_path=section,
                extra={"product_group": "g1", "base_name": "MADCAT Black Spin"},
            )
            for n in ("2.70M 40-150G", "3.00M 50-200G")
        ]
        refine_categories(items)
        self.assertEqual({i.category_path for i in items}, {f"{section} > Meškerykočiai"})

    def test_group_prefers_nemus_category(self):
        from apps.ingestion.dto import NormalizedItem
        from apps.suppliers.adapters.albatros import refine_categories

        nemus = "Vandens pramogos > Hidrokostiumai > Vyriški hidrokostiumai"
        items = [
            NormalizedItem(
                external_id=n, sku=n, name=f"Jobe Perth {n}", category_path=path,
                extra={"product_group": "g1", "nemus_category": path == nemus},
            )
            for n, path in (("L", "JOBE Wetsuits"), ("XL", "JOBE Wetsuits"), ("2XL", nemus))
        ]
        refine_categories(items)
        self.assertEqual({i.category_path for i in items}, {nemus})

    def test_group_gets_one_category(self):
        from apps.ingestion.dto import NormalizedItem
        from apps.suppliers.adapters.albatros import refine_categories

        section = "Suit - Jacket+Trousers"
        items = [
            NormalizedItem(
                external_id=n, sku=n, name=f"Savage Gear WP Suit {n}", category_path=section,
                extra={"product_group": "g1", "base_name": "Savage Gear WP Suit"},
            )
            for n in ("Jacket L", "Jacket XL", "Bib Trousers L")
        ]
        refine_categories(items)
        self.assertEqual({i.category_path for i in items}, {f"{section} > Striukės"})
