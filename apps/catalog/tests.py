from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from apps.catalog.attributes import resolve_code
from apps.catalog.models import UnmappedAttribute
from apps.catalog.parts import is_part, product_is_part
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

    def test_source_precision_is_kept_without_conversion(self):
        # 0.15 g ir 0.2 g plūdės – skirtingi variantai, ne vienas kodas.
        self.assertEqual(parse_value("0.15g", "number", "g").code, "0-15-g")
        self.assertEqual(parse_value("0.2g", "number", "g").code, "0-2-g")
        self.assertEqual(parse_value("0.1234", "number", "mm").num, Decimal("0.1234"))

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

    def test_slash_range(self):
        # '0.8/1.5m' – plikas skaičius + skaičius su vienetu -> intervalas.
        pv = parse_value("0.8/1.5m", "number", "m")
        self.assertEqual(pv.num, Decimal("0.8"))
        self.assertEqual(pv.num_max, Decimal("1.5"))
        self.assertEqual(pv.display, "0,8-1,5 m")
        # Bet ne trupmenos / kabliuko dydžiai.
        self.assertIsNone(parse_value("1/0", "number", "").num)
        self.assertIsNone(parse_value("10g-1/0", "number", "g").num)

    def test_sections_sum(self):
        # '1+1pc' = 1 dalis + antgalis -> 2 sekcijos; '2+2' -> 4.
        for raw, expected in (("1+1pc", "2"), ("2+2", "4"), ("2+1", "3")):
            pv = parse_value(raw, "number", "", axis="sections")
            self.assertEqual(pv.num, Decimal(expected), raw)
            self.assertEqual(pv.display, expected, raw)
        # Tik sections ašiai – '9+1' be konteksto lieka neparsinta.
        self.assertIsNone(parse_value("9+1", "number", "").num)


class ProfileForTests(TestCase):
    def test_feeder_before_rods(self):
        from apps.catalog.profiles import profile_for

        # 'feeder' match'ina rods ctx – feeders turi eiti pirmiau.
        self.assertEqual(
            profile_for("meškerės > feeder rods > method").code, "rods"
        )
        self.assertEqual(
            profile_for("feeder cages > method feeders").code, "feeders"
        )
        self.assertEqual(profile_for("spinning rods").code, "rods")
        self.assertEqual(profile_for("braided lines").code, "lines")
        self.assertEqual(profile_for("luggage").code, "generic")


class SplitCompositeTests(SimpleTestCase):
    """F3: kompozitinių tiekėjų 'size' reikšmių skaldymas (MOSS <ROZMER>
    formos iš moss_rozmer_values.txt)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from apps.catalog.profiles import DEFAULT_PROFILE, PROFILES

        cls.profiles = {p.code: p for p in PROFILES}
        cls.generic = DEFAULT_PROFILE

    def _split(self, code, value):
        from apps.catalog.profiles import split_composite

        return split_composite(value, self.profiles[code])

    def test_lines_triplets(self):
        produced, leftover = self._split("lines", "0,08 mm / 1,2 kg / 100 m")
        self.assertEqual(
            produced,
            {
                "line_diameter": "0,08 mm",
                "line_strength": "1,2 kg",
                "line_length": "100 m",
            },
        )
        self.assertEqual(leftover, "")
        produced, leftover = self._split("lines", "0.6mm/4kg/10m")
        self.assertEqual(
            produced,
            {
                "line_diameter": "0.6mm",
                "line_strength": "4kg",
                "line_length": "10m",
            },
        )
        self.assertEqual(leftover, "")
        # Inline be skirtukų.
        produced, _ = self._split("lines", "0,10mm 6,8kg 130m")
        self.assertEqual(produced["line_diameter"], "0,10mm")
        self.assertEqual(produced["line_strength"], "6,8kg")
        self.assertEqual(produced["line_length"], "130m")
        # lbs -> strength (konvertuojama vėliau, parse_value).
        produced, _ = self._split("lines", "15lbs 20m")
        self.assertEqual(produced, {"line_strength": "15lbs", "line_length": "20m"})

    def test_lines_hash_and_pe_stay_leftover(self):
        produced, leftover = self._split("lines", "#1 / 10cm / 16 kg")
        self.assertEqual(produced["line_length"], "10cm")
        self.assertEqual(produced["line_strength"], "16 kg")
        self.assertEqual(leftover, "#1")
        produced, leftover = self._split("lines", "#1/7kg")
        self.assertEqual(produced, {"line_strength": "7kg"})
        self.assertEqual(leftover, "#1")
        produced, leftover = self._split("lines", "#10+16mm")
        self.assertEqual(produced, {})
        self.assertEqual(leftover, "#10+16mm")

    def test_lines_slash_range_and_dash_range_kept(self):
        produced, leftover = self._split("lines", "0.8/1.5m")
        self.assertEqual(produced, {"line_length": "0.8/1.5m"})
        self.assertEqual(leftover, "")
        produced, _ = self._split("lines", "0,20mm-0,35mm")
        self.assertEqual(produced, {"line_diameter": "0,20mm-0,35mm"})

    def test_rods_length_cast_sections(self):
        produced, leftover = self._split("rods", "170cm/150g/2 składy")
        self.assertEqual(
            produced,
            {
                "rod_length_m": "170cm",
                "casting_weight": "150g",
                "sections": "2 składy",
            },
        )
        self.assertEqual(leftover, "")
        produced, _ = self._split("rods", "2,7m/2,75lbs/2 składy")
        self.assertEqual(produced["rod_length_m"], "2,7m")
        self.assertEqual(produced["testcurve"], "2,75lbs")
        self.assertEqual(produced["sections"], "2 składy")
        # Dviejų vienetų ilgis -> metrinis laimi prieš imperialą.
        produced, _ = self._split("rods", "12ft/360cm/3,00lbs/2 składy")
        self.assertEqual(produced["rod_length_m"], "360cm")
        self.assertEqual(produced["testcurve"], "3,00lbs")

    def test_rods_leftover_and_czesci(self):
        produced, leftover = self._split("rods", "240cm/50g/Tele")
        self.assertEqual(produced["rod_length_m"], "240cm")
        self.assertEqual(produced["casting_weight"], "50g")
        self.assertEqual(leftover, "Tele")
        produced, leftover = self._split("rods", "360cm/2 części + 6T + 0,30mm")
        self.assertEqual(produced["rod_length_m"], "360cm")
        self.assertEqual(produced["sections"], "2 części")
        self.assertEqual(leftover, "6T 0,30mm")

    def test_lures_weight_hook_and_colorish_leftover(self):
        produced, leftover = self._split("lures", "10g-1/0")
        self.assertEqual(produced, {"weight": "10g", "hook_size": "1/0"})
        self.assertEqual(leftover, "")
        produced, _ = self._split("lures", "1.5g-6")
        self.assertEqual(produced, {"weight": "1.5g", "hook_size": "6"})
        produced, leftover = self._split("lures", "10,5 cm/PERCHY")
        self.assertEqual(produced, {"lure_size": "10,5 cm"})
        self.assertEqual(leftover, "PERCHY")
        produced, leftover = self._split("lures", "12,5g/9cm/Susp./MIRROR")
        self.assertEqual(produced, {"weight": "12,5g", "lure_size": "9cm"})
        self.assertEqual(leftover, "Susp. MIRROR")
        produced, leftover = self._split("lures", "10g/roz.3/BLOODY")
        self.assertEqual(produced["weight"], "10g")
        self.assertEqual(produced["hook_size"], "roz.3")
        self.assertEqual(leftover, "BLOODY")
        produced, leftover = self._split("lures", "0.5g AURO Hook #8 Snap 00")
        self.assertEqual(produced["weight"], "0.5g")
        self.assertEqual(produced["hook_size"], "#8")
        self.assertEqual(leftover, "AURO Hook Snap 00")

    def test_feeders_size_weight(self):
        produced, leftover = self._split("feeders", "X-Small 20g")
        self.assertEqual(produced, {"size": "X-Small", "weight": "20g"})
        self.assertEqual(leftover, "")
        produced, _ = self._split("feeders", "Large 60g")
        self.assertEqual(produced, {"size": "Large", "weight": "60g"})

    def test_rigs_hook_diameter_length(self):
        produced, leftover = self._split("rigs", "12cm / 0,10mm / BARB #6 / Sting 5mm")
        self.assertEqual(produced["line_length"], "12cm")
        self.assertEqual(produced["line_diameter"], "0,10mm")
        self.assertEqual(produced["hook_size"], "#6")
        self.assertEqual(leftover, "BARB Sting 5mm")
        produced, leftover = self._split("rigs", "20 cm / 17lbs / #4")
        self.assertEqual(produced["line_length"], "20 cm")
        self.assertEqual(produced["line_strength"], "17lbs")
        self.assertEqual(produced["hook_size"], "#4")
        self.assertEqual(leftover, "")
        produced, leftover = self._split("rigs", "1m/#1/0/0,26mm/5szt")
        self.assertEqual(produced["hook_size"], "#1/0")
        self.assertEqual(produced["line_diameter"], "0,26mm")
        self.assertEqual(produced["line_length"], "1m")
        self.assertEqual(leftover, "5szt")

    def test_unprofiled_no_split(self):
        from apps.catalog.profiles import split_composite

        produced, leftover = split_composite(
            "0,08 mm / 1,2 kg / 100 m", self.generic
        )
        self.assertEqual(produced, {})
        self.assertEqual(leftover, "0,08 mm / 1,2 kg / 100 m")
        # hooks profilis turi taisykles (džigo galvutės '10g-1/0'):
        # '10/0' -> hook_size, modelio žymuo lieka.
        produced, leftover = split_composite("10/0 FLAT-O", self.profiles["hooks"])
        self.assertEqual(produced, {"hook_size": "10/0"})
        self.assertEqual(leftover, "FLAT-O")


class IsPartTests(TestCase):
    """Atsarginių dalių atpažinimas (apps.catalog.parts)."""

    def _offer(self, **kwargs):
        from apps.ingestion.models import Offer
        from apps.suppliers.models import Supplier

        supplier, _ = Supplier.objects.get_or_create(
            code="shimano", defaults={"name": "Shimano"}
        )
        return Offer.objects.create(
            supplier=supplier,
            external_id=kwargs.pop("external_id", "X-1"),
            name=kwargs.pop("name", "Item"),
            **kwargs,
        )

    def test_reel_parts_category(self):
        self.assertTrue(is_part(self._offer(category_raw="Reel parts")))

    def test_spools_category(self):
        self.assertTrue(is_part(self._offer(category_raw="Spools")))

    def test_spares_categories(self):
        for cat in (
            "Rod Sections (excl poles)",
            "Leaders & spares",
            "Aventus Pole Topkit/Spares",
            "Carp Porter Spare Parts",
            "Rive Spare parts",
        ):
            offer = self._offer(external_id=cat, category_raw=cat)
            self.assertTrue(is_part(offer), cat)

    def test_sub_category_and_product_fallback(self):
        from apps.catalog.models import Product

        product = Product.objects.create(
            sku="p-1", name="Reel", category_raw="Reel parts"
        )
        self.assertTrue(is_part(self._offer(product=product)))
        self.assertTrue(
            is_part(self._offer(external_id="s-1", sub_category_raw="Spools"))
        )

    def test_spare_spool_name(self):
        offer = self._offer(name="FX spare spool 4000", category_raw="Reels")
        self.assertTrue(is_part(offer))
        self.assertTrue(
            is_part(
                self._offer(external_id="r-1", name="Replacement drag knob")
            )
        )

    def test_normal_rod_is_not_part(self):
        offer = self._offer(
            external_id="rod-1", name="Speedmaster AX Spinning 270M",
            category_raw="Spinning rods",
        )
        self.assertFalse(is_part(offer))

    def test_product_is_part_majority(self):
        part = self._offer(external_id="pa-1", category_raw="Reel parts")
        reel = self._offer(external_id="re-1", category_raw="Reels")
        self.assertTrue(product_is_part([part, part, reel]))
        self.assertFalse(product_is_part([part, reel, reel]))
        self.assertFalse(product_is_part([]))
