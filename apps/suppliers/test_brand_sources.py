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

    def test_svs_prefix_is_stripped_to_albatros_code(self):
        self.assertEqual(shopify.albatros_code(" svs65045 "), "65045")
        self.assertEqual(shopify.albatros_code("SVSX1"), "SVSX1")
        self.assertEqual(shopify.albatros_code("1652720"), "1652720")

    def test_default_title_option_is_dropped(self):
        product = {
            "title": "Rod",
            "options": [{"name": "Title"}],
            "variants": [{"sku": "SVS72812", "option1": "Default Title"}],
        }
        parsed = shopify.parse_product(product, "https://x")
        self.assertEqual(parsed["sections"][0]["rows"], {"72812": {}})

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


class ScrapeBrandStaleTests(TestCase):
    def _run(self, complete):
        def finished(*_):
            yield (
                "de",
                sportfishtackle.BASE + "kept.html",
                sportfishtackle.parse_page(PAGE),
            )
            return complete

        with (
            mock.patch.object(sportfishtackle, "crawl", side_effect=finished),
            mock.patch.object(shopify, "crawl", return_value=iter([])),
        ):
            call_command(
                "scrape_manufacturer", source="savage-gear", stdout=mock.Mock()
            )

    def test_complete_crawl_clears_codes_of_removed_pages(self):
        from apps.suppliers.models import ManufacturerContent

        gone = ManufacturerContent.objects.create(
            source="savage-gear",
            url=sportfishtackle.BASE + "gone.html",
            language="de",
            codes=["1"],
        )
        other = ManufacturerContent.objects.create(
            source="savage-gear",
            url="https://www.savagegear.com/products/x",
            language="en",
            codes=["2"],
        )
        self._run(complete=True)
        gone.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(gone.codes, [])
        self.assertEqual(other.codes, ["2"])

    def test_incomplete_crawl_keeps_codes(self):
        from apps.suppliers.models import ManufacturerContent

        gone = ManufacturerContent.objects.create(
            source="savage-gear",
            url=sportfishtackle.BASE + "gone.html",
            language="de",
            codes=["1"],
        )
        self._run(complete=False)
        gone.refresh_from_db()
        self.assertEqual(gone.codes, ["1"])


class ShopifyBadResponseTests(SimpleTestCase):
    def test_non_json_page_stops_without_raising(self):
        s = mock.Mock()
        bad = _response()
        bad.json.side_effect = ValueError("not json")
        s.get.return_value = bad
        gen = shopify.crawl("https://x", s=s, sleep=lambda _: None)
        with self.assertRaises(StopIteration) as stop:
            next(gen)
        self.assertFalse(stop.exception.value)


class BrandSourceTests(SimpleTestCase):
    def _patched(self):
        from apps.suppliers.manufacturers.brands import BrandSource

        page = ("de", "https://t/1", {})
        return BrandSource("savage-gear"), mock.patch.multiple(
            "apps.suppliers.manufacturers.brands",
            shopify=mock.Mock(
                SITES=shopify.SITES,
                LANGUAGE="en",
                crawl=lambda *a: iter(
                    [("en", "https://s/1", {}), ("en", "https://s/2", {})]
                ),
            ),
            sportfishtackle=mock.Mock(
                SLUG_PREFIXES=sportfishtackle.SLUG_PREFIXES,
                LANGUAGE="de",
                crawl=lambda *a: iter([page]),
            ),
        )

    def test_languages_select_sources(self):
        source, patch = self._patched()
        with patch:
            urls = [url for _, url, _ in source.crawl(languages=("de",))]
        self.assertEqual(urls, ["https://t/1"])

    def test_limit_is_shared_across_sources(self):
        source, patch = self._patched()
        with patch:
            urls = [url for _, url, _ in source.crawl(languages=("de", "en"), limit=1)]
        self.assertEqual(urls, ["https://s/1"])


class ShopifyIncompleteTests(SimpleTestCase):
    def _result(self, payload):
        s = mock.Mock()
        response = _response()
        response.json.return_value = payload
        s.get.return_value = response
        gen = shopify.crawl("https://x", s=s, sleep=lambda _: None)
        with self.assertRaises(StopIteration) as stop:
            next(gen)
        return stop.exception.value

    def test_missing_products_key_is_incomplete(self):
        self.assertFalse(self._result({"errors": "x"}))

    def test_empty_first_page_is_incomplete(self):
        self.assertFalse(self._result({"products": []}))


class SportfishtackleIncompleteTests(SimpleTestCase):
    def test_sitemap_without_brand_urls_is_incomplete(self):
        gen = sportfishtackle.crawl(
            "savage-gear",
            s=mock.Mock(get=mock.Mock(return_value=_response(text="<urlset/>"))),
            sleep=lambda _: None,
        )
        with self.assertRaises(StopIteration) as stop:
            next(gen)
        self.assertFalse(stop.exception.value)

    def test_page_without_product_data_is_incomplete(self):
        sitemap = (
            "<loc>https://www.sportfiskeprylar.se/sv/artiklar/savage-gear-x.html</loc>"
        )
        s = mock.Mock()
        s.get.side_effect = [
            _response(text=sitemap),
            _response(content=b"<html><h1>Maintenance</h1></html>"),
        ]
        gen = sportfishtackle.crawl("savage-gear", s=s, sleep=lambda _: None)
        with self.assertRaises(StopIteration) as stop:
            next(gen)
        self.assertFalse(stop.exception.value)


_ZUKLYS_PAGE = """<html><head><script type="application/ld+json">{"@type": "BreadcrumbList"}</script>
<script type="application/ld+json">{"@context": "http://schema.org", "@type": "Product",
"name": "49-40-30d-6 Guminukai Crazy Fish NIMBLE 1,6' 49-40-30d-6",
"image": "https://zuklys.lt/image/cache/catalog/n-800x800.jpg"}</script></head><body>
<div id="tab-description" class="product-desc"></div>
<div id="tab-specification"><table><tr><td>Ilgis (mm)</td><td>40mm</td></tr>
<tr><td>Spalva</td><td>30d</td></tr></table></div></body></html>"""


class ZuklysParseTests(SimpleTestCase):
    def test_page_with_code_first_in_name(self):
        from apps.suppliers.manufacturers import zuklys

        parsed = zuklys.parse_page(_ZUKLYS_PAGE.encode(), "49-40-30d-6")
        self.assertEqual(parsed["codes"], ["49-40-30D-6"])
        self.assertEqual(parsed["series"], "Guminukai Crazy Fish NIMBLE 1,6'")
        self.assertEqual(parsed["images"], ["https://zuklys.lt/image/cache/catalog/n-800x800.jpg"])
        self.assertEqual(
            parsed["sections"][0]["rows"], {"49-40-30D-6": {"Ilgis (mm)": "40mm", "Spalva": "30d"}}
        )

    def test_page_for_other_code_is_rejected(self):
        from apps.suppliers.manufacturers import zuklys

        self.assertIsNone(zuklys.parse_page(_ZUKLYS_PAGE.encode(), "49-40"))

    def test_sitemap_candidates_match_code_as_slug_part(self):
        from apps.suppliers.manufacturers import zuklys

        xml = "<urlset>" + "".join(
            f"<url><loc>{u}</loc></url>"
            for u in (
                "https://zuklys.lt/lt/a/143803-spiningine-rite-tica-wild-fox-lt.html",
                "https://zuklys.lt/lt/a/49-40-30d-6-guminukai-crazy-fish-nimble-49-40-30d-6-lt.html",
                "https://zuklys.lt/lt/a/1438030-kita-preke-lt.html",
                "http://169.254.169.254/143803-x-lt.html",
                "https://zuklys.lt.evil.com/143803-x-lt.html",
            )
        ) + "</urlset>"
        index = zuklys.sitemap_index(xml)
        self.assertEqual(
            zuklys.candidates("143803", index),
            ["https://zuklys.lt/lt/a/143803-spiningine-rite-tica-wild-fox-lt.html"],
        )
        self.assertEqual(
            zuklys.candidates("49-40-30D-6", index),
            ["https://zuklys.lt/lt/a/49-40-30d-6-guminukai-crazy-fish-nimble-49-40-30d-6-lt.html"],
        )
        self.assertEqual(zuklys.candidates("99999", index), [])

    def test_candidates_starting_with_code_come_first(self):
        from apps.suppliers.manufacturers import zuklys

        slugs = ["a-143803-x", "b-143803-y", "c-143803", "143803-tica-lustre"]
        xml = "".join(f"<loc>https://zuklys.lt/lt/{s}-lt.html</loc>" for s in slugs)
        found = zuklys.candidates("143803", zuklys.sitemap_index(xml))
        self.assertEqual(len(found), zuklys.MAX_CANDIDATES)
        self.assertEqual(found[0], "https://zuklys.lt/lt/143803-tica-lustre-lt.html")

    def test_crawl_uses_sitemap_and_tries_candidates_until_code_matches(self):
        from apps.suppliers.manufacturers import zuklys

        xml = (
            "<urlset><url><loc>https://zuklys.lt/lt/x/49-40-30d-6-a-lt.html</loc></url>"
            "<url><loc>https://zuklys.lt/lt/x/49-40-30d-6-b-lt.html</loc></url></urlset>"
        )
        pages = [
            _response(text=xml),
            _response(content=b"<html></html>"),
            _response(content=_ZUKLYS_PAGE.encode()),
        ]
        urls = []

        class S:
            def get(self, url, **kw):
                urls.append(url)
                return pages.pop(0)

        out = list(zuklys.crawl(["49-40-30d-6"], s=S(), sleep=lambda *_: None))
        self.assertEqual([url for _, url, _ in out], ["https://zuklys.lt/lt/x/49-40-30d-6-b-lt.html"])
        self.assertEqual(urls[0], zuklys.SITEMAP)
        self.assertFalse(any("route=product/search" in u for u in urls))

    def test_crawl_without_sitemap_yields_nothing(self):
        from apps.suppliers.manufacturers import zuklys

        class S:
            def get(self, url, **kw):
                return _response(status=503)

        self.assertEqual(list(zuklys.crawl(["1"], s=S(), sleep=lambda *_: None)), [])


_NEMUS_PAGE = """<html><head><script type="application/ld+json">{"@context": "http://schema.org/",
"@type": "Product", "name": "DAVIKLIS HUMMINBIRD XI 14 HW", "model": "710313-1"}</script></head><body>
<h1>DAVIKLIS HUMMINBIRD XI 14 HW</h1>
<div class="swiper-slide"><img src="https://nemus.lt/image/cache/catalog/a-550x550.webp"
 data-largeimg="https://nemus.lt/image/cache/catalog/a-1000x1000.webp"/></div>
<div class="swiper-slide"><img data-largeimg="https://evil.example/x.webp"/></div>
<ul class="nav nav-tabs"><li><a href="#tab-a" data-toggle="tab">Aprašymas</a></li>
<li><a href="#tab-b" data-toggle="tab">Komentarai</a></li></ul>
<div id="tab-a"><div class="block-content"><p><b>XI 14 HW</b></p><p>Keitiklis 130–250 kHz.</p></div></div>
<div id="tab-b"><div class="block-content"><h4>Rašyti apžvalgą</h4></div></div>
</body></html>"""


class NemusTests(TestCase):
    def test_parse_page_code_images_description(self):
        from apps.suppliers.manufacturers import nemus

        parsed = nemus.parse_page(_NEMUS_PAGE.encode())
        self.assertEqual(parsed["codes"], ["710313-1"])
        self.assertEqual(parsed["images"], ["https://nemus.lt/image/cache/catalog/a-1000x1000.webp"])
        self.assertEqual(parsed["intro"], "XI 14 HW\nKeitiklis 130–250 kHz.")
        self.assertEqual(parsed["series"], "DAVIKLIS HUMMINBIRD XI 14 HW")

    def test_page_without_code_is_skipped(self):
        from apps.suppliers.manufacturers import nemus

        self.assertIsNone(nemus.parse_page(b"<html><body><h1>Kategorija</h1></body></html>"))

    def test_crawl_sitemap_only_wanted_codes_and_hosts(self):
        from apps.suppliers.manufacturers import nemus

        xml = (
            "<urlset><url><loc>https://nemus.lt/a-1</loc></url>"
            "<url><loc>https://nemus.lt/index.php?route=product/product&amp;product_id=2</loc></url>"
            "<url><loc>https://nemus.lt.evil.com/c</loc></url></urlset>"
        )
        other = _NEMUS_PAGE.replace("710313-1", "999")
        pages = {"https://nemus.lt/a-1": _NEMUS_PAGE, "https://nemus.lt/index.php?route=product/product&product_id=2": other}
        urls = []

        class R:
            def __init__(self, body):
                self.status_code, self.text, self.content, self.headers = 200, body, body.encode(), {}

        class S:
            headers = {}

            def get(self, url, **_):
                urls.append(url)
                return R(xml if url == nemus.SITEMAP else pages[url])

        out = list(nemus.crawl(["710313-1"], s=S(), sleep=lambda *_: None))
        self.assertEqual([url for _, url, _ in out], ["https://nemus.lt/a-1"])
        self.assertEqual(urls, [nemus.SITEMAP, *pages])

    def test_empty_page_is_skipped(self):
        from apps.suppliers.manufacturers import nemus

        self.assertIsNone(nemus.parse_page(b""))

    def test_crawl_without_codes_fetches_nothing(self):
        from apps.suppliers.manufacturers import nemus

        class S:
            headers: dict = {}

            def get(self, url, **_):
                raise AssertionError(url)

        self.assertEqual(list(nemus.crawl([], s=S(), sleep=lambda *_: None)), [])
