from django.test import SimpleTestCase, TestCase

from apps.suppliers.manufacturers.sportex import parse_page

HTML = b"""<html><head>
<link rel="alternate" hreflang="de" href="https://www.sportex.de/rf/">
<link rel="alternate" hreflang="pl" href="https://www.sportex.de/pl/rf/">
<meta property="og:title" content="Rapid Feeder RS-2">
</head><body><main><h6>RAPID FEEDER RS-2</h6><h5>Rapid Feeder RS-2</h5>
<p>Kompakte Version unserer beliebten Feederruten-Serie fuer schwierige Spots.</p>
<h5>Rapid Feeder RS-2 Light / XS</h5>
<p>Die Rapid Feeder Light aus japanischem High-Performance-Carbon.</p>
<h5>Rapid Feeder RS-2 Medium / XS</h5>
<p>Die Rapid Feeder Medium fuer mittelschwere Angelei in Fliessgewaessern.</p>
<h3>Informationen zur Rapid Feeder RS-2</h3>
<p>Seaguide D-Hook: fuer uns macht diese Hakenoese am meisten Sinn.</p>
<h4>Rapid Feeder Light ``XS`` RS-2</h4>
<table><tr><th>Modell</th><th>Art.-Nr.</th><th>Teile</th><th>UVP*</th></tr>
<tr><td>LF3324</td><td>162334</td><td>2+3</td><td>189,95</td></tr></table>
<img src="https://www.sportex.de/wp-content/uploads/2025/10/rf.jpg">
<img src="https://www.sportex.de/wp-content/uploads/2025/10/rf-300x66.jpg">
</main></body></html>"""


class SportexParseTests(SimpleTestCase):
    def test_parse_sections_rows_and_languages(self):
        page = parse_page(HTML)
        self.assertEqual(page["series"], "Rapid Feeder RS-2")
        self.assertIn("Kompakte Version", page["intro"])
        self.assertEqual(page["codes"], ["162334"])
        section = page["sections"][0]
        self.assertEqual(section["heading"], "Rapid Feeder Light ``XS`` RS-2")
        self.assertNotIn("Seaguide", page["intro"] + section["text"])
        self.assertIn("japanischem", section["text"])
        self.assertEqual(
            section["rows"]["162334"], {"Modell": "LF3324", "Teile": "2+3"}
        )
        self.assertEqual(page["languages"]["pl"], "https://www.sportex.de/pl/rf/")
        self.assertEqual(
            page["images"], ["https://www.sportex.de/wp-content/uploads/2025/10/rf.jpg"]
        )


class ClimaxSeriesMatchTests(TestCase):
    def test_codes_by_series_tokens(self):
        from apps.ingestion.models import Offer
        from apps.suppliers.management.commands.scrape_manufacturer import _series_codes
        from apps.suppliers.models import Supplier

        supplier = Supplier.objects.create(code="ockert", name="Ockert")
        Offer.objects.create(
            supplier=supplier,
            external_id="a1",
            name="x",
            brand_name_raw="Climax",
            base_name="Climax Seamaster Fluorocarbon",
            sub_category_raw="Seamaster",
        )
        Offer.objects.create(
            supplier=supplier,
            external_id="b1",
            name="x",
            brand_name_raw="Climax",
            base_name="Climax Seamaster Braid",
            sub_category_raw="Seamaster",
        )
        self.assertEqual(_series_codes("climax", "SEAMASTER Fluorocarbon"), ["A1"])


class KeepMostSpecificTests(TestCase):
    def test_code_kept_on_longest_series(self):
        from apps.suppliers.management.commands.scrape_manufacturer import (
            _keep_most_specific,
        )
        from apps.suppliers.models import ManufacturerContent

        def row(url, tokens):
            return ManufacturerContent.objects.create(
                source="climax",
                url=url,
                language="de",
                codes=["A1"],
                specs={"series_tokens": tokens, "sections": [{"rows": {"A1": {}}}]},
            )

        general, specific = (
            row("https://x/strylon/", 1),
            row("https://x/strylon-aqua/", 2),
        )
        self.assertEqual(_keep_most_specific("climax"), 1)
        general.refresh_from_db()
        specific.refresh_from_db()
        self.assertEqual(general.codes, [])
        self.assertEqual(specific.codes, ["A1"])
