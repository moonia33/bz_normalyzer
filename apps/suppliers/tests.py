from decimal import Decimal

from django.test import TestCase

from apps.suppliers.adapters.base import BaseAdapter
from apps.suppliers.adapters.generic_excel import GenericExcelAdapter
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
