from decimal import Decimal

from django.test import TestCase

from apps.catalog.attributes import resolve_code
from apps.catalog.models import UnmappedAttribute
from apps.catalog.unmapped import collect
from apps.catalog.values import parse_value


class ResolveCodeTests(TestCase):
    def test_alias(self):
        self.assertEqual(resolve_code("strength_lb", ""), "line_strength")
        self.assertEqual(resolve_code("line-test-kg", ""), "line_strength")
        self.assertEqual(resolve_code("section", ""), "sections")

    def test_context_rules(self):
        self.assertEqual(
            resolve_code("length", "spinning rods"), "rod_length_m"
        )
        self.assertEqual(
            resolve_code("length", "braided lines"), "line_length"
        )
        self.assertIsNone(resolve_code("length", "luggage"))
        self.assertEqual(
            resolve_code("diameter", "braided lines"), "line_diameter"
        )
        self.assertEqual(resolve_code("diameter", "luggage"), "diameter")
        self.assertEqual(
            resolve_code("diameter", "landing nets"), "hoop_diameter"
        )

    def test_variantas_and_unknown(self):
        self.assertIsNone(resolve_code("variantas", "anything"))
        self.assertIsNone(resolve_code("totally_unknown", ""))

    def test_registered_code_returns_itself(self):
        self.assertEqual(resolve_code("casting_weight", ""), "casting_weight")

    def test_word_boundaries(self):
        self.assertIsNone(resolve_code("length", "Clothing > Hoodies"))
        self.assertEqual(
            resolve_code("length", "Spinning rods"), "rod_length_m"
        )
        self.assertEqual(
            resolve_code("diameter", "Internet accessories"), "diameter"
        )
        self.assertEqual(
            resolve_code("diameter", "landing nets"), "hoop_diameter"
        )


class ParseValueTests(TestCase):
    def test_number_mm(self):
        for raw in ("0,26", "0.26mm", "0,26 mm"):
            pv = parse_value(raw, "number", "mm")
            self.assertEqual(pv.num, Decimal("0.26"), raw)
            self.assertEqual(pv.code, "0-26-mm", raw)
            self.assertEqual(pv.display, "0,26 mm", raw)

    def test_lb_to_kg(self):
        pv = parse_value("20lb", "number", "kg")
        self.assertEqual(pv.num, Decimal("9.07"))
        self.assertEqual(pv.display, "9,07 kg (20 lb)")

    def test_feet_inches(self):
        pv = parse_value("13'3\"", "number", "ft")
        self.assertEqual(pv.num, Decimal("13.25"))

    def test_range(self):
        pv = parse_value("10-40g", "range", "g")
        self.assertEqual(pv.num, Decimal("10"))
        self.assertEqual(pv.num_max, Decimal("40"))
        self.assertEqual(pv.code, "10-40-g")

    def test_meters_and_cm(self):
        self.assertEqual(parse_value("2.70m", "number", "m").num, Decimal("2.7"))
        self.assertEqual(parse_value("270cm", "number", "m").num, Decimal("2.7"))

    def test_oz_to_g(self):
        self.assertEqual(parse_value("3oz", "number", "g").num, Decimal("85"))

    def test_suspicious_weight(self):
        pv = parse_value("227000 g", "number", "g")
        self.assertTrue(pv.suspicious)

    def test_enum_size(self):
        pv = parse_value("XXL", "enum", "")
        self.assertEqual(pv.code, "2xl")
        self.assertEqual(pv.display, "2XL")

    def test_unparsed_reports(self):
        with collect():
            pv = parse_value("abc", "number", "mm")
        self.assertIsNone(pv.num)
        self.assertEqual(pv.display, "abc")
        ua = UnmappedAttribute.objects.get(kind="unparsed_value")
        self.assertEqual(ua.sample_values, ["abc"])


class ParseValueExtendedTests(TestCase):
    def test_range_units_both_sides(self):
        pv = parse_value("10g-40g", "range", "g")
        self.assertEqual((pv.num, pv.num_max), (Decimal("10"), Decimal("40")))
        pv = parse_value("8-16 lbs", "range", "lb")
        self.assertEqual((pv.num, pv.num_max), (Decimal("8"), Decimal("16")))
        self.assertIsNone(parse_value("10g-40cm", "range", "g").num)

    def test_count_suffixes(self):
        for raw in ("2pc", "3 pcs", "2 sk", "3-tlg", "2 sections", "2-piece"):
            self.assertEqual(parse_value(raw, "number", "").num, Decimal(raw[0]))
        self.assertEqual(parse_value("4pc+tips", "number", "").num, Decimal("4"))
        self.assertIsNone(parse_value("9+1", "number", "").num)

    def test_unit_aliases(self):
        self.assertEqual(parse_value("30 gr.", "number", "g").num, Decimal("30"))
        self.assertEqual(parse_value("2 mtr", "number", "m").num, Decimal("2"))

    def test_dual_unit(self):
        pv = parse_value('11cm/4.25"', "number", "cm")
        self.assertEqual(pv.num, Decimal("11"))
        self.assertEqual(pv.display, "11 cm")
        self.assertEqual(parse_value("8oz / 224 gr.", "number", "g").num, Decimal("224"))
        self.assertEqual(parse_value("2.70m / 9'", "number", "m").num, Decimal("2.7"))
        self.assertIsNone(parse_value("0,26mm/5kg", "number", "mm").num)
