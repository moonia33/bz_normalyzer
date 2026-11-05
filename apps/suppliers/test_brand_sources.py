from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from apps.suppliers.manufacturers import http, shopify, sportfishtackle

PRODUCT = {
    "title": "3D  4Play Herring",
    "handle": "3d-4play-herring",
    "product_type": "Swimbaits",
    "body_html": "<p>Lifelike <b>herring</b>.</p><ul><li>3D scan</li><li>ABS lip</li></ul>",
    "options": [{"name": "Color"}, {"name": "Bait Length"}],
    "variants": [
        {"sku": "1652720", "option1": "Herring", "option2": "13cm"},
        {"sku": " 1652721 ", "option1": "Firetiger", "option2": "13cm"},
        {"sku": "", "option1": "Ghost", "option2": "13cm"},
    ],
    "images": [
        {"src": "https://cdn.shopify.com/a.jpg?v=1"},
        {"src": "https://cdn.shopify.com/a.jpg?v=2"},
        {"src": "https://cdn.shopify.com/b.jpg"},
    ],
}

PAGE = b"""<html lang='DE'><head>
<script type="application/ld+json">{"@type": "WebSite", "name": "x"}</script>
<script type="application/ld+json">{"@context": "http://schema.org/", "@type": "Product",
 "category": "Spinnruten", "name": "Savage Gear Parabellum CCS",
 "description": "Perfekte Toray Carbon Blanks", "image": "https://www.sportfishtackle.de/bilder/artiklar/62388r.jpg?m=1",
 "offers": {"@type": "AggregateOffer", "offers": [
  {"@type": "Offer", "gtin12": "5706301623867", "sku": "62386", "name": "Savage Gear Parabellum CCS 9'2'' 279cm 7-23g - 2sec"},
  {"@type": "Offer", "gtin12": "", "sku": "62388", "name": "Savage Gear Parabellum CCS 10'1'' 307cm 12-32g - 2sec"}]}}
</script></head><body>
<h1 id="ArtikelnamnFalt">Savage Gear Parabellum CCS</h1>
<a href="/bilder/artiklar/zoom/62388_1.jpg?m=1"><img src="/bilder/artiklar/zoom/62388_1.jpg"></a>
<img src="/bilder/artiklar/zoom/62388r_1.jpg">
<div class="product__text--long col-xs-12"><span>Perfekte Toray Carbon Blanks.</span><br><ul><li>Toray Carbon</li><li>Sin Coil Guides</li></ul></div>
<div class="product__text--long mobile"><span>Perfekte Toray Carbon Blanks.</span></div>
<table><tr><td class="TeknSpec_Rad1_Vanster">Aktion:</td><td class="TeknSpec_Rad1_Hoger"> Moderate Fast </td></tr>
<tr><td class="TeknSpec_Rad2_Vanster">Rutenteile:</td><td class="TeknSpec_Rad2_Hoger"> 2-Teilig </td></tr>
<tr><td class="TeknSpec_Rad1_Vanster">Aktion:</td><td class="TeknSpec_Rad1_Hoger"> Moderate Fast </td></tr></table>
</body></html>"""


def _response(status=200, payload=None, content=b"", text="", headers=None):
    response = mock.Mock(
        status_code=status, content=content, text=text, headers=headers or {}
    )
    response.json.return_value = payload or {}
    return response


class ShopifyParseTests(SimpleTestCase):
    def test_product_is_base_and_variants_carry_codes(self):
        parsed = shopify.parse_product(PRODUCT, "https://www.savagegear.com")
        self.assertEqual(parsed["series"], "3D 4Play Herring")
        self.assertEqual(parsed["codes"], ["1652720", "1652721"])
        rows = parsed["sections"][0]["rows"]
        self.assertEqual(rows["1652721"], {"Color": "Firetiger", "Bait Length": "13cm"})
        self.assertEqual(
            parsed["images"],
            ["https://cdn.shopify.com/a.jpg", "https://cdn.shopify.com/b.jpg"],
        )
        self.assertEqual(parsed["intro"], "Lifelike herring.\n3D scan\nABS lip")
        self.assertEqual(parsed["category"], "Swimbaits")

    def test_default_title_option_is_dropped(self):
        product = {
            "title": "Rod",
            "options": [{"name": "Title"}],
            "variants": [{"sku": "SVS72812", "option1": "Default Title"}],
        }
        parsed = shopify.parse_product(product, "https://x")
        self.assertEqual(parsed["sections"][0]["rows"], {"SVS72812": {}})

    def test_crawl_pages_until_empty(self):
        s = mock.Mock()
        s.get.side_effect = [
            _response(
                payload={"products": [PRODUCT, {"handle": "no-codes", "variants": []}]}
            ),
            _response(payload={"products": []}),
        ]
        out = list(
            shopify.crawl("https://www.savagegear.com", s=s, sleep=lambda _: None)
        )
        self.assertEqual(len(out), 1)
        lang, url, _parsed = out[0]
        self.assertEqual(
            (lang, url), ("en", "https://www.savagegear.com/products/3d-4play-herring")
        )
        self.assertEqual(s.get.call_args_list[1].kwargs["params"]["page"], 2)


class SportfishtackleParseTests(SimpleTestCase):
    def test_parse_offers_eans_specs_images(self):
        parsed = sportfishtackle.parse_page(PAGE)
        self.assertEqual(parsed["series"], "Savage Gear Parabellum CCS")
        self.assertEqual(parsed["codes"], ["62386", "62388"])
        self.assertEqual(parsed["eans"], {"62386": "5706301623867"})
        section = parsed["sections"][0]
        self.assertEqual(
            section["rows"]["62386"]["name"],
            "Savage Gear Parabellum CCS 9'2'' 279cm 7-23g - 2sec",
        )
        self.assertEqual(section["text"], "Aktion: Moderate Fast\nRutenteile: 2-Teilig")
        self.assertEqual(
            parsed["intro"],
            "Perfekte Toray Carbon Blanks.\n• Toray Carbon\n• Sin Coil Guides",
        )
        self.assertEqual(
            parsed["images"],
            [
                "https://www.sportfishtackle.de/bilder/artiklar/zoom/62388_1.jpg",
                "https://www.sportfishtackle.de/bilder/artiklar/zoom/62388r_1.jpg",
            ],
        )

    def test_page_without_product_has_no_codes(self):
        self.assertEqual(
            sportfishtackle.parse_page(b"<html><body>404</body></html>")["codes"], []
        )

    def test_product_urls_by_brand_prefix(self):
        sitemap = (
            "<loc>https://www.sportfiskeprylar.se/sv/artiklar/savage-gear-parabellum-ccs.html</loc>"
            "<loc>https://www.sportfiskeprylar.se/sv/artiklar/savage-gear-parabellum-ccs.html</loc>"
            "<loc>https://www.sportfiskeprylar.se/sv/artiklar/dam-effzett.html</loc>"
            "<loc>https://www.sportfiskeprylar.se/sv/fiskespon</loc>"
        )
        self.assertEqual(
            sportfishtackle.product_urls(sitemap, "savage-gear"),
            [
                "https://www.sportfishtackle.de/de/artiklar/savage-gear-parabellum-ccs.html"
            ],
        )


class PoliteGetTests(SimpleTestCase):
    def test_429_backs_off_then_succeeds(self):
        s = mock.Mock()
        s.get.side_effect = [
            _response(429, headers={"Retry-After": "7"}),
            _response(200),
        ]
        sleeps = []
        response = http.get(s, "https://x", delay=0.2, sleep=sleeps.append)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sleeps, [1.0, 7.0, 1.0])

    def test_gives_up_without_raising(self):
        s = mock.Mock()
        s.get.side_effect = http.requests.ConnectionError("down")
        self.assertIsNone(http.get(s, "https://x", sleep=lambda _: None))
        self.assertEqual(s.get.call_count, http.MAX_RETRIES + 1)


class ScrapeBrandCommandTests(TestCase):
    def test_brand_source_stores_content_by_brand_slug(self):
        from apps.suppliers.models import ManufacturerContent

        parsed = sportfishtackle.parse_page(PAGE)
        with (
            mock.patch.object(
                sportfishtackle,
                "crawl",
                return_value=iter([("de", "https://t/p.html", parsed)]),
            ),
            mock.patch.object(shopify, "crawl", return_value=iter([])),
        ):
            call_command(
                "scrape_manufacturer", source="savage-gear", stdout=mock.Mock()
            )
        content = ManufacturerContent.objects.get(source="savage-gear")
        self.assertEqual(content.language, "de")
        self.assertEqual(content.codes, ["62386", "62388"])
        self.assertEqual(content.specs["eans"], {"62386": "5706301623867"})
        self.assertEqual(
            content.section_for("62388")["heading"], "Savage Gear Parabellum CCS"
        )
