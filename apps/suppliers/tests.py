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
