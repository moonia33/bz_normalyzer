from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.catalog.models import (
    Brand, Category, Product, ProductText, UnmappedAttribute,
)
from apps.catalog_export.models import ExportRun
from apps.catalog_export.services import (
    ShopClient,
    _offer_context,
    build_catalog_payload,
    build_stock_payload,
    payload_sha256,
    run_export,
)
from apps.corecfg.models import Language
from apps.ingestion.models import Offer
from apps.suppliers.models import Supplier


class ExportPayloadTests(TestCase):
    def setUp(self):
        self.lang_en = Language.objects.create(
            code="en", name="English", is_base=True
        )
        self.lang_pl = Language.objects.create(code="pl", name="Polish")
        self.supplier = Supplier.objects.create(
            code="fox",
            name="FOX",
            vat_rate=Decimal("0.21"),
            selling_price_includes_vat=True,
        )
        self.brand = Brand.objects.create(name="Fox Rage", slug="fox-rage")
        self.category = Category.objects.create(name="Meškerės", slug="meskeres")
        self.product = Product.objects.create(
            sku="fox-AAA-1",
            name="Variant One",
            ean="1111111111111",
            product_group="GRP-1",
            brand=self.brand,
            category=self.category,
            description="Base EN description",
            images=["https://img.example.com/a.jpg"],
            attributes={
                "specs": {
                    "Gear Ratio": "5.1:1",
                    "Unit Height": "10",
                    "Color": "Black",
                }
            },
            shipping_weight_kg=Decimal("0.180"),
            purchase_price=Decimal("8.00"),
        )
        ProductText.objects.create(
            product=self.product,
            language=self.lang_pl,
            name="Wariant Jeden",
            description="Opis PL",
        )
        self.offer = Offer.objects.create(
            supplier=self.supplier,
            external_id="AAA-1",
            sku="AAA-1",
            name="Variant One",
            ean="1111111111111",
            product=self.product,
            stock=5,
            is_available=True,
            is_published=True,
            purchase_price=Decimal("8.00"),
            selling_price=Decimal("12.10"),
            size_raw="Medium",
            attributes={"specs": {"Color": "Black/Grey"}},
        )

    def test_catalog_payload_shape_and_vat_math(self):
        payload = build_catalog_payload()
        self.assertEqual(payload["format_version"], 2)
        self.assertEqual(payload["source"], "normalyzer")
        self.assertEqual(payload["mode"], "upsert")
        self.assertEqual(len(payload["items"]), 1)

        item = payload["items"][0]
        self.assertEqual(item["code"], "fox-AAA-1")
        self.assertEqual(item["ean"], "1111111111111")
        self.assertEqual(item["brand_code"], "fox-rage")
        self.assertTrue(item["brand_name"])
        self.assertEqual(item["category"], "meskeres")
        self.assertEqual(item["product_group_code"], "GRP-1")
        # 12.10 su PVM -> 10.00 neto
        self.assertEqual(item["price_net"], "10.00")
        self.assertEqual(item["cost_net"], "8.00")
        self.assertEqual(item["weight_g"], 180)
        self.assertEqual(item["tax_class"], "standard")
        # 'Gear Ratio' -> feature; 'Unit Height' -> ignoruojama.
        # 'Color'/'Size' ašys su viena reikšme produkte -> demote į feature.
        feature_codes = {f["code"] for f in item["features"]}
        self.assertEqual(feature_codes, {"gear-ratio", "color", "size"})
        gear = next(f for f in item["features"] if f["code"] == "gear-ratio")
        self.assertEqual(gear["value"], "5-1-1")
        self.assertEqual(gear["value_translations"]["en"], "5.1:1")
        self.assertEqual(item["images"][0]["url"], "https://img.example.com/a.jpg")

        translations = item["translations"]
        self.assertEqual(translations["en"]["name"], "Variant One")
        self.assertEqual(translations["pl"]["name"], "Wariant Jeden")
        # lt privaloma – fallback į bazinį tekstą kol nėra vertimo
        self.assertEqual(translations["lt"]["name"], "Variant One")
        self.assertTrue(translations["lt"]["slug"])

        variants = item["variants"]
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0]["code"], "AAA-1")
        self.assertEqual(variants[0]["ean"], "1111111111111")
        # Vienintelis variantas -> ašys nevarijuoja -> options tuščios,
        # reikšmės demote į item.features.
        self.assertEqual(variants[0]["options"], {})
        self.assertEqual(variants[0]["price_net"], "10.00")
        self.assertEqual(variants[0]["stock"], {"fox": 5})

    def test_cross_supplier_same_ean_merges_into_one_variant(self):
        moss = Supplier.objects.create(code="moss", name="MOSS")
        Offer.objects.create(
            supplier=moss,
            external_id="M-9",
            sku="M-9",
            name="Variant One",
            ean="1111111111111",
            product=self.product,
            stock=3,
            is_published=True,
            selling_price=Decimal("12.10"),
        )
        payload = build_catalog_payload()
        variants = payload["items"][0]["variants"]
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0]["stock"], {"fox": 5, "moss": 3})

    def test_unpublished_offers_excluded(self):
        self.offer.is_published = False
        self.offer.save()
        payload = build_catalog_payload()
        self.assertEqual(payload["items"], [])

    def test_fox_variant_keeps_only_main_image(self):
        # Fox media_cloud_links: offer'is turi visa galerija (Main + roles);
        # variante eksportuojam tik pirma (Main), galerija lieka produkte.
        self.offer.images = [
            "https://cdn.example.com/main.jpg",
            "https://cdn.example.com/studio1.jpg",
            "https://cdn.example.com/studio2.jpg",
        ]
        self.offer.save()
        payload = build_catalog_payload()
        variant = payload["items"][0]["variants"][0]
        self.assertEqual(
            [i["url"] for i in variant["images"]],
            ["https://cdn.example.com/main.jpg"],
        )

    def test_non_fox_variant_keeps_all_offer_images(self):
        self.supplier.code = "korda"
        self.supplier.save()
        self.offer.images = [
            "https://cdn.example.com/a.jpg",
            "https://cdn.example.com/b.jpg",
        ]
        self.offer.save()
        payload = build_catalog_payload()
        variant = payload["items"][0]["variants"][0]
        self.assertEqual(len(variant["images"]), 2)

    def test_stock_payload(self):
        payload = build_stock_payload("fox")
        self.assertEqual(payload["warehouse_code"], "fox")
        self.assertEqual(payload["items"], [{"code": "AAA-1", "qty": 5}])

    def test_shared_art_no_between_suppliers_merges_variant(self):
        """Tas pats art-no iš dviejų tiekėjų = tas pats variantas; likučiai per warehouse."""
        ccm = Supplier.objects.create(code="ccm", name="CCM")
        Offer.objects.create(
            supplier=ccm,
            external_id="AAA-1",
            sku="AAA-1",
            name="Kitoks AAA-1",
            ean="2222222222222",
            product=self.product,
            stock=2,
            is_published=True,
            selling_price=Decimal("9.90"),
        )
        payload = build_catalog_payload()
        variants = payload["items"][0]["variants"]
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0]["code"], "AAA-1")
        self.assertEqual(variants[0]["stock"], {"fox": 5, "ccm": 2})
        stock = build_stock_payload("ccm")
        self.assertEqual(stock["items"], [{"code": "AAA-1", "qty": 2}])

    def test_warehouse_override(self):
        self.supplier.export_warehouse_code = "fox-main"
        self.supplier.save()
        payload = build_stock_payload("fox")
        self.assertEqual(payload["warehouse_code"], "fox-main")

    def _make_offer(self, external_id, name, attrs, **kwargs):
        defaults = dict(
            supplier=self.supplier,
            sku=external_id,
            product=self.product,
            is_published=True,
            is_available=True,
            selling_price=Decimal("12.10"),
            purchase_price=Decimal("8.00"),
        )
        defaults.update(kwargs)
        return Offer.objects.create(
            external_id=external_id, name=name, attributes=attrs, **defaults
        )

    def test_minimal_options_prefers_size_and_name_match(self):
        """Horizon Holdall atvejis: size vienintelis skiria variantus –
        unit_* ir model nebetampa options, nueina į features."""
        self._make_offer(
            "GLU132",
            "Matrix Horizon 2 Rod Stiff Holdall 1.75m",
            {
                "axes": {
                    "size": "1.75m",
                    "color": "Black/Lime",
                    "model": "2 Rod",
                    "unit-width": "22cm",
                    "unit-height": "17cm",
                    "unit-length": "175cm",
                }
            },
        )
        self._make_offer(
            "GLU133",
            "Matrix Horizon 2 Rod Stiff Holdall 1.95m",
            {
                "axes": {
                    "size": "1.95m",
                    "color": "Black/Lime",
                    "model": "2 Rod",
                    "unit-width": "22cm",
                    "unit-height": "17cm",
                    "unit-length": "195cm",
                }
            },
        )
        payload = build_catalog_payload()
        item = payload["items"][0]
        options = {v["code"]: v["options"] for v in item["variants"]}
        self.assertEqual(options["GLU132"], {"size": "1-75m"})
        self.assertEqual(options["GLU133"], {"size": "1-95m"})
        labels = item["option_labels"]["size"]
        self.assertEqual(labels["1-75m"], "1,75 m")
        self.assertEqual(labels["1-95m"], "1,95 m")
        feature_codes = {f["code"] for f in item["features"]}
        # Nevarijuojančios ašys lieka product features; varijuojanti
        # unit_length nueina į variant.features (kiekvienas turi savo reikšmę).
        for demoted in ("model", "unit-width", "unit-height"):
            self.assertIn(demoted, feature_codes)
        self.assertNotIn("unit-length", feature_codes)
        variant_features = {
            v["code"]: {f["code"]: f["value_translations"]["en"] for f in v["features"]}
            for v in item["variants"]
        }
        self.assertEqual(variant_features["GLU132"].get("unit-length"), "175 cm")
        self.assertEqual(variant_features["GLU133"].get("unit-length"), "195 cm")

    def test_options_extend_only_when_combo_collides(self):
        """Antra ašis pridedama tik jei pirmos neužtenka atskirti."""
        self._make_offer(
            "C-1", "Reel 2500 Black",
            {"axes": {"size": "2500", "color": "Black"}},
        )
        self._make_offer(
            "C-2", "Reel 2500 Red",
            {"axes": {"size": "2500", "color": "Red"}},
        )
        payload = build_catalog_payload()
        options = {v["code"]: v["options"] for v in payload["items"][0]["variants"]}
        # size nevarijuoja -> feature; vienintelė varijuojanti ašis = color
        self.assertEqual(options["C-1"], {"color": "black"})
        self.assertEqual(options["C-2"], {"color": "red"})
        item = payload["items"][0]
        # AAA-1 offer'is turi size_raw='Medium' -> size varijuoja per
        # variantus -> eina į variant.features, ne product features.
        feature_codes = {f["code"] for f in item["features"]}
        self.assertNotIn("size", feature_codes)
        variant_features = {
            v["code"]: {f["code"]: f["value_translations"]["en"] for f in v["features"]}
            for v in item["variants"]
        }
        self.assertEqual(variant_features["C-1"].get("size"), "2500")
        self.assertEqual(variant_features["AAA-1"].get("size"), "M")

    def test_second_axis_added_only_when_first_collides(self):
        """Size vien neužtenka (dvi eilutės su ta pačia reikšme) ->
        pridedama kita ašis, kuri išspjaudžia kolizijas."""
        self._make_offer(
            "D-1", "Chair Camo",
            {"axes": {"size": "Std", "color": "Camo"}},
        )
        self._make_offer(
            "D-2", "Chair Green",
            {"axes": {"size": "Std", "color": "Green"}},
        )
        self._make_offer(
            "D-3", "Chair XL Camo",
            {"axes": {"size": "XL", "color": "Camo"}},
        )
        payload = build_catalog_payload()
        options = {v["code"]: v["options"] for v in payload["items"][0]["variants"]}
        self.assertEqual(options["D-1"], {"color": "camo", "size": "standard"})
        self.assertEqual(options["D-2"], {"color": "green", "size": "standard"})
        self.assertEqual(options["D-3"], {"color": "camo", "size": "xl"})

    def test_empty_none_and_zero_values_ignored(self):
        """'None'/'0'/tuščios reikšmės netampa nei options, nei features."""
        self.product.attributes = {
            "features": {"material-fabric": "None", "sections": "4"},
            "compliance": {"material-fabric": "none"},
        }
        self.product.save(update_fields=["attributes"])
        self._make_offer(
            "N-1", "Chair",
            {"axes": {"size": "None", "unit-height": "0"}},
        )
        self._make_offer(
            "N-2", "Chair XL",
            {"axes": {"size": "XL", "unit-height": "0"}},
        )
        payload = build_catalog_payload()
        item = payload["items"][0]
        options = {v["code"]: v["options"] for v in item["variants"]}
        # size 'None' dingsta -> N-1 neturi option; N-2 turi 'xl'
        self.assertEqual(options["N-1"], {})
        self.assertEqual(options["N-2"], {"size": "xl"})
        self.assertNotIn("unit-height", item["option_labels"])
        feature_codes = {f["code"] for f in item["features"]}
        self.assertNotIn("material-fabric", feature_codes)
        self.assertIn("sections", feature_codes)

    def test_sold_as_pack_variant_uses_pack_prices_and_barcode(self):
        """'Dėžė kaip prekė': ean=pack barcode, kainos ×trade_pack_qty."""
        self.offer.delete()
        self._make_offer(
            "NZS-1",
            "Zander Pro Shad 7.5cm UV Perch 60pcs",
            {},
            ean="5056212116980",
            pack_barcode="5056212116980",
            pack_unit="pcs",
            trade_pack_qty=60,
            sold_as_pack=True,
            purchase_price=Decimal("0.50"),
            selling_price=Decimal("1.00"),
        )
        payload = build_catalog_payload()
        variant = payload["items"][0]["variants"][0]
        # 1.00 gross -> 0.83 net (suapvalinta /1.21 = 0.8264..); ×60 -> 49.59? net*sėk
        # price_net skaičiuojamas iš selling×qty -> 60.00 gross -> 49.59 net
        self.assertEqual(variant["ean"], "5056212116980")
        self.assertEqual(variant["sold_as_pack"], True)
        self.assertEqual(variant["trade_pack_qty"], 60)
        self.assertEqual(variant["pack_barcode"], "5056212116980")
        self.assertEqual(variant["pack_price"], "49.59")
        self.assertEqual(variant["pack_purchase_price"], "30.00")
        self.assertEqual(variant["price_net"], "49.59")
        self.assertEqual(variant["cost_net"], "30.00")

    def test_regular_offer_exports_pack_fields(self):
        self.offer.delete()
        self._make_offer(
            "P-1",
            "Both EAN item",
            {},
            ean="1111111111111",
            pack_barcode="5555555555551",
            pack_unit="pcs",
            pack_quantity=2,
            trade_pack_qty=5,
            sold_as_pack=False,
            purchase_price=Decimal("3.50"),
            selling_price=Decimal("6.99"),
        )
        payload = build_catalog_payload()
        variant = payload["items"][0]["variants"][0]
        self.assertEqual(variant["ean"], "1111111111111")
        self.assertEqual(variant["pack_barcode"], "5555555555551")
        self.assertEqual(variant["pack_unit"], "pcs")
        self.assertEqual(variant["pack_quantity"], 2)
        self.assertEqual(variant["trade_pack_qty"], 5)
        self.assertFalse(variant["sold_as_pack"])
        # 6.99 gross -> 5.78 net; ×5 -> 28.88; cost 3.50×5=17.50
        self.assertEqual(variant["price_net"], "5.78")
        self.assertEqual(variant["pack_price"], "28.88")
        self.assertEqual(variant["pack_purchase_price"], "17.50")

    def test_is_part_flag_from_offer_category(self):
        self.offer.category_raw = "Reel parts"
        self.offer.save(update_fields=["category_raw"])
        payload = build_catalog_payload()
        self.assertTrue(payload["items"][0]["is_part"])

    def test_is_part_flag_false_for_normal_product(self):
        payload = build_catalog_payload()
        self.assertFalse(payload["items"][0]["is_part"])

    def test_is_part_majority_rule(self):
        # Vienas iš dviejų offer'ių dalis -> lygiai 50% -> True.
        Offer.objects.create(
            supplier=self.supplier,
            external_id="AAA-2",
            sku="AAA-2",
            name="Variant Two spare",
            product=self.product,
            category_raw="Reel parts",
            stock=1,
            is_published=True,
            selling_price=Decimal("5.00"),
        )
        payload = build_catalog_payload()
        self.assertTrue(payload["items"][0]["is_part"])
        # Dar vienas ne-dalis -> 1/3 -> False.
        Offer.objects.create(
            supplier=self.supplier,
            external_id="AAA-3",
            sku="AAA-3",
            name="Variant Three",
            product=self.product,
            category_raw="Reels",
            stock=1,
            is_published=True,
            selling_price=Decimal("5.00"),
        )
        payload = build_catalog_payload()
        self.assertFalse(payload["items"][0]["is_part"])

    def test_payload_hash_stable(self):
        p1 = build_catalog_payload()
        p2 = build_catalog_payload()
        p1.pop("generated_at")
        p2.pop("generated_at")
        self.assertEqual(payload_sha256(p1), payload_sha256(p2))


class PushAsyncTests(TestCase):
    PAYLOAD = {"format_version": 1, "items": []}

    def _push(self, post_response, get_responses):
        with (
            override_settings(SHOP_API_BASE_URL="http://shop.test"),
            patch.object(
                ShopClient, "post", return_value=post_response
            ) as mock_post,
            patch.object(
                ShopClient, "get", side_effect=get_responses
            ) as mock_get,
        ):
            run = run_export(
                ExportRun.Kind.CATALOG, self.PAYLOAD, push=True
            )
        return run, mock_post, mock_get

    def test_post_uses_apply_async(self):
        run, mock_post, _ = self._push(
            {"id": 7, "status": "done", "failed_items": 0, "total_items": 0},
            [],
        )
        self.assertIn("apply_async=true", mock_post.call_args[0][0])
        self.assertEqual(run.status, ExportRun.Status.PUSHED)

    def test_terminal_status_from_post_skips_polling(self):
        run, _, mock_get = self._push(
            {"id": 7, "status": "done", "failed_items": 0, "total_items": 5},
            [],
        )
        self.assertEqual(run.status, ExportRun.Status.PUSHED)
        self.assertEqual(run.shop_response["status"], "done")
        mock_get.assert_not_called()

    def test_queued_run_is_polled_until_done(self):
        run, _, mock_get = self._push(
            {"id": 7, "status": "queued"},
            [
                {"id": 7, "status": "applying"},
                {"id": 7, "status": "done", "failed_items": 0, "total_items": 5},
            ],
        )
        self.assertEqual(run.status, ExportRun.Status.PUSHED)
        self.assertEqual(run.error_message, "")
        self.assertEqual(run.shop_response["status"], "done")
        self.assertEqual(mock_get.call_count, 2)

    def test_done_with_errors_keeps_pushed_and_records_message(self):
        run, _, _ = self._push(
            {"id": 7, "status": "queued"},
            [
                {
                    "id": 7,
                    "status": "done_with_errors",
                    "failed_items": 3,
                    "total_items": 100,
                }
            ],
        )
        self.assertEqual(run.status, ExportRun.Status.PUSHED)
        self.assertEqual(
            run.error_message, "shop import #7: 3 of 100 items failed"
        )

    def test_failed_run_marks_export_failed(self):
        run, _, _ = self._push(
            {"id": 7, "status": "queued"},
            [{"id": 7, "status": "failed", "error_message": "Invalid file: x"}],
        )
        self.assertEqual(run.status, ExportRun.Status.FAILED)
        self.assertEqual(run.error_message, "Invalid file: x")

    def test_wait_for_run_timeout(self):
        with (
            patch.object(ShopClient, "get", return_value={"status": "applying"}),
            patch("apps.catalog_export.services.time.sleep"),
            self.assertRaises(TimeoutError),
        ):
            ShopClient("http://shop.test").wait_for_run(
                1, poll_interval=0, timeout=0
            )


class AttributeGateTests(TestCase):
    """F1: registry vartai – nežinomi kodai metami + report'inami."""

    def setUp(self):
        Language.objects.create(code="en", name="English", is_base=True)
        self.supplier = Supplier.objects.create(
            code="fox", name="FOX", vat_rate=Decimal("0.21"),
            selling_price_includes_vat=True,
        )
        self.product = Product.objects.create(
            sku="fox-G-1", name="Rod", is_active=True,
        )

    def _offer(self, attrs):
        return Offer.objects.create(
            supplier=self.supplier, external_id="G-1", sku="G-1",
            name="Rod", product=self.product, is_published=True,
            is_available=True, selling_price=Decimal("10.00"),
            attributes=attrs,
        )

    def test_gate_alias_and_unmapped(self):
        self._offer(
            {"axes": {"strength_lb": "20lb", "foo": "x"}}
        )
        payload = build_catalog_payload()
        item = payload["items"][0]
        codes = {f["code"] for f in item["features"]}
        self.assertIn("line-strength", codes)
        self.assertNotIn("foo", codes)
        ua = UnmappedAttribute.objects.get(code="foo", kind="unmapped_code")
        self.assertEqual(ua.count, 1)
        self.assertEqual(ua.sample_values, ["x"])
        feature = next(f for f in item["features"] if f["code"] == "line-strength")
        self.assertEqual(feature["unit"], "kg")
        self.assertEqual(feature["datatype"], "number")
        self.assertEqual(feature["value_num"], "9.07")

    def test_variantas_becomes_variant_name(self):
        self._offer(
            {"axes": {"variantas": "12cm Red", "size": "M"}}
        )
        payload = build_catalog_payload()
        item = payload["items"][0]
        variant = item["variants"][0]
        self.assertEqual(variant["name"], "12cm Red")
        codes = {f["code"] for f in item["features"]}
        self.assertNotIn("variantas", codes)
        self.assertNotIn("variantas", variant.get("options", {}))
        self.assertFalse(
            UnmappedAttribute.objects.filter(code="variantas").exists()
        )

    def test_option_value_meta_for_numeric_axis(self):
        # casting-weight yra 'rods' profilio ašis – kategorija duoda profilį.
        self.product.category = Category.objects.create(
            name="Spinning rods", slug="gate-rods"
        )
        self.product.save(update_fields=["category"])
        self._offer({"axes": {"casting_weight": "10-40g"}})
        Offer.objects.create(
            supplier=self.supplier, external_id="G-2", sku="G-2",
            name="Rod 2", product=self.product, is_published=True,
            is_available=True, selling_price=Decimal("10.00"),
            attributes={"axes": {"casting_weight": "50-80g"}},
        )
        payload = build_catalog_payload()
        item = payload["items"][0]
        meta = item["option_value_meta"].get("casting-weight")
        self.assertIsNotNone(meta)
        self.assertIn("10-40-g", meta)
        self.assertEqual(meta["10-40-g"]["num"], "10")
        self.assertEqual(meta["10-40-g"]["num_max"], "40")
        self.assertEqual(meta["10-40-g"]["unit"], "g")
        # Variant features turi value_num/unit
        variant = next(
            v for v in item["variants"] if v["options"].get("casting-weight") == "10-40-g"
        )
        for f in variant.get("features", []):
            self.assertIn("value_num", f)
            self.assertIn("unit", f)


class ProfileAxesTests(TestCase):
    """Deklaratyvūs per-kategoriją option profiliai + all-or-nothing."""

    def setUp(self):
        Language.objects.create(code="en", name="English", is_base=True)
        self.supplier = Supplier.objects.create(
            code="fox", name="FOX", vat_rate=Decimal("0.21"),
            selling_price_includes_vat=True,
        )
        self.category = Category.objects.create(
            name="Spinning rods", slug="spinning-rods"
        )
        self.product = Product.objects.create(
            sku="fox-P-1", name="Rod", is_active=True, category=self.category,
        )

    def _offer(self, eid, name, attrs):
        return Offer.objects.create(
            supplier=self.supplier, external_id=eid, sku=eid, name=name,
            product=self.product, is_published=True, is_available=True,
            selling_price=Decimal("10.00"), attributes=attrs,
        )

    def test_profile_gates_option_axes(self):
        # rods profilis: (rod_length_m, testcurve, casting_weight, sections,
        # model). 'line-diameter' varijuoja bet profilyje nėra -> ne option.
        self._offer("P-1", "Rod A", {"axes": {
            "rod-length-m": "2.70m", "line-diameter": "0.20mm"}})
        self._offer("P-2", "Rod B", {"axes": {
            "rod-length-m": "3.00m", "line-diameter": "0.25mm"}})
        payload = build_catalog_payload()
        options = {v["code"]: v["options"] for v in payload["items"][0]["variants"]}
        self.assertEqual(options["P-1"], {"rod-length-m": "2-7-m"})
        self.assertEqual(options["P-2"], {"rod-length-m": "3-m"})
        # line-diameter vis tiek yra variant.features (option ⊂ features)
        v1 = next(v for v in payload["items"][0]["variants"] if v["code"] == "P-1")
        fcodes = {f["code"] for f in v1["features"]}
        self.assertIn("line-diameter", fcodes)
        self.assertIn("rod-length-m", fcodes)

    def test_collision_zeroes_options(self):
        # casting-weight profilyje yra, bet abiem offer'iams ta pati
        # reikšmė+combo -> nepilnai atskiria -> options = []
        self._offer("P-1", "Rod A", {"axes": {
            "casting-weight": "10-40g", "variantas": "A"}})
        self._offer("P-2", "Rod B", {"axes": {
            "casting-weight": "10-40g", "variantas": "B"}})
        payload = build_catalog_payload()
        item = payload["items"][0]
        for v in item["variants"]:
            self.assertEqual(v["options"], {})

    def test_generated_variant_names(self):
        self._offer("P-1", "Rod A", {"axes": {
            "casting-weight": "10-40g", "sections": "2"}})
        self._offer("P-2", "Rod B", {"axes": {
            "casting-weight": "50-80g", "sections": "3"}})
        payload = build_catalog_payload()
        names = {v["code"]: v["name"] for v in payload["items"][0]["variants"]}
        # Profilio tvarka: casting_weight prieš sections
        self.assertEqual(names["P-1"], "10-40 g · 2")
        self.assertEqual(names["P-2"], "50-80 g · 3")


class CompositeAxisTests(TestCase):
    """F3: kompozitinės 'size'/'variantas' reikšmės -> kanoninės ašys,
    liekana -> varianto vardas."""

    def setUp(self):
        Language.objects.create(code="en", name="English", is_base=True)
        self.supplier = Supplier.objects.create(
            code="moss", name="MOSS", vat_rate=Decimal("0.21"),
            selling_price_includes_vat=True,
        )

    def _product(self, category_name):
        category = Category.objects.create(
            name=category_name, slug=category_name.lower().replace(" ", "-")
        )
        return Product.objects.create(
            sku=f"moss-{category.slug}", name="Item", is_active=True,
            category=category,
        )

    def _offer(self, product, eid, name, size_raw="", attrs=None):
        return Offer.objects.create(
            supplier=self.supplier, external_id=eid, sku=eid, name=name,
            product=product, is_published=True, is_available=True,
            selling_price=Decimal("10.00"), size_raw=size_raw,
            attributes=attrs or {},
        )

    def test_lines_rozmer_splits_to_canonical_axes(self):
        product = self._product("Braided lines")
        self._offer(product, "L-1", "Line A", size_raw="0,10mm 6,8kg 130m")
        self._offer(product, "L-2", "Line B", size_raw="0,12mm 8,7kg 150m")
        payload = build_catalog_payload()
        item = payload["items"][0]
        options = {v["code"]: v["options"] for v in item["variants"]}
        # Profilio lines ašys: line_diameter pirma – ji atskiria.
        self.assertEqual(options["L-1"], {"line-diameter": "0-1-mm"})
        self.assertEqual(options["L-2"], {"line-diameter": "0-12-mm"})
        # strength/length – variant.features (ne profile.axes option).
        vf = {
            v["code"]: {f["code"]: f["value_translations"]["en"]
                        for f in v["features"]}
            for v in item["variants"]
        }
        self.assertEqual(vf["L-1"].get("line-strength"), "6,8 kg")
        self.assertEqual(vf["L-1"].get("line-length"), "130 m")
        self.assertEqual(vf["L-2"].get("line-strength"), "8,7 kg")
        # generic 'size' pilnai suskaldyta – jokio 'size' atributo.
        self.assertNotIn("size", vf["L-1"])
        self.assertNotIn("size", item["option_labels"])
        feature_codes = {f["code"] for f in item["features"]}
        self.assertNotIn("size", feature_codes)

    def test_rods_length_cast_sections(self):
        product = self._product("Spinning rods")
        self._offer(product, "R-1", "Rod A", size_raw="170cm/150g/2 składy")
        self._offer(product, "R-2", "Rod B", size_raw="180cm/10-30g/3 składy")
        payload = build_catalog_payload()
        item = payload["items"][0]
        options = {v["code"]: v["options"] for v in item["variants"]}
        self.assertEqual(options["R-1"], {"rod-length-m": "1-7-m"})
        self.assertEqual(options["R-2"], {"rod-length-m": "1-8-m"})
        vf = {
            v["code"]: {f["code"]: f["value_translations"]["en"]
                        for f in v["features"]}
            for v in item["variants"]
        }
        self.assertEqual(vf["R-1"].get("casting-weight"), "150 g")
        self.assertEqual(vf["R-1"].get("sections"), "2")
        self.assertEqual(vf["R-2"].get("casting-weight"), "10-30 g")
        self.assertEqual(vf["R-2"].get("sections"), "3")

    def test_feeder_xsmall_20g_gives_size_and_weight_options(self):
        product = self._product("Feeder cages")
        self._offer(product, "F-1", "Feeder A", size_raw="X-Small 20g")
        self._offer(product, "F-2", "Feeder B", size_raw="X-Small 30g")
        self._offer(product, "F-3", "Feeder C", size_raw="Small 20g")
        payload = build_catalog_payload()
        item = payload["items"][0]
        options = {v["code"]: v["options"] for v in item["variants"]}
        self.assertEqual(
            options["F-1"], {"size": "xs", "weight": "20-g"}
        )
        self.assertEqual(
            options["F-2"], {"size": "xs", "weight": "30-g"}
        )
        self.assertEqual(
            options["F-3"], {"size": "s", "weight": "20-g"}
        )

    def test_lures_weight_and_hook_size(self):
        product = self._product("Lures")
        self._offer(product, "J-1", "Jig A", size_raw="10g-1/0")
        self._offer(product, "J-2", "Jig B", size_raw="12g-2/0")
        payload = build_catalog_payload()
        item = payload["items"][0]
        options = {v["code"]: v["options"] for v in item["variants"]}
        self.assertEqual(options["J-1"], {"weight": "10-g"})
        self.assertEqual(options["J-2"], {"weight": "12-g"})
        vf = {
            v["code"]: {f["code"]: f["value_translations"]["en"]
                        for f in v["features"]}
            for v in item["variants"]
        }
        self.assertEqual(vf["J-1"].get("hook-size"), "1/0")
        self.assertEqual(vf["J-2"].get("hook-size"), "2/0")

    def test_lure_leftover_goes_to_variant_name_and_size(self):
        product = self._product("Lures")
        self._offer(product, "C-1", "Worm A", size_raw="10,5 cm/PERCHY")
        self._offer(product, "C-2", "Worm B", size_raw="10,5 cm/MIRROR")
        payload = build_catalog_payload()
        item = payload["items"][0]
        names = {v["code"]: v["name"] for v in item["variants"]}
        # Liekana -> varianto vardas (color-ish, ne feature).
        self.assertEqual(names["C-1"], "PERCHY")
        self.assertEqual(names["C-2"], "MIRROR")
        # Liekana nukreipiama per leftover_axis: lures -> 'color'
        # (PERCHY/MIRROR yra spalvų/pattern'ų vardai, ne 'size').
        options = {v["code"]: v["options"] for v in item["variants"]}
        self.assertEqual(options["C-1"], {"color": "perchy"})
        self.assertEqual(options["C-2"], {"color": "mirror"})
        # lure_size vienoda -> demoted į product features.
        feature_codes = {f["code"] for f in item["features"]}
        self.assertIn("lure-size", feature_codes)
        self.assertNotIn("size", feature_codes)

    def test_single_token_value_is_not_composite(self):
        product = self._product("Braided lines")
        self._offer(product, "S-1", "Line A", size_raw="0,10mm")
        self._offer(product, "S-2", "Line B", size_raw="0,12mm")
        payload = build_catalog_payload()
        item = payload["items"][0]
        vf = {
            v["code"]: {f["code"]: f["value_translations"]["en"]
                        for f in v["features"]}
            for v in item["variants"]
        }
        # '0,10mm' vienas token'as -> neskaldoma, lieka 'size' ašis.
        self.assertEqual(vf["S-1"].get("size"), "0,10 mm")
        self.assertNotIn("line-diameter", vf["S-1"])

    def test_variantas_label_composite_becomes_axes(self):
        product = self._product("Braided lines")
        self._offer(
            product, "V-1", "Line A",
            attrs={"axes": {"variantas": "0,7mm 7m"}},
        )
        self._offer(
            product, "V-2", "Line B",
            attrs={"axes": {"variantas": "0,8mm 10m"}},
        )
        payload = build_catalog_payload()
        item = payload["items"][0]
        options = {v["code"]: v["options"] for v in item["variants"]}
        self.assertEqual(options["V-1"], {"line-diameter": "0-7-mm"})
        self.assertEqual(options["V-2"], {"line-diameter": "0-8-mm"})
        names = {v["code"]: v["name"] for v in item["variants"]}
        # 'variantas' pilnai suskaldyta -> vardas generuojamas iš meta.
        self.assertIn("0,7 mm", names["V-1"])
        self.assertIn("7 m", names["V-1"])


class InferredContextTests(TestCase):
    """Ašys-inferuota kategorija: 'Uncategorized' offer'iai su
    rod/reel ašimis gauna profilį iš ašių, ne iš kategorijos."""

    def setUp(self):
        Language.objects.create(code="en", name="English", is_base=True)
        self.supplier = Supplier.objects.create(
            code="shimano", name="Shimano", vat_rate=Decimal("0.21"),
            selling_price_includes_vat=True,
        )
        self.product = Product.objects.create(
            sku="sh-I-1", name="Rod", is_active=True,
        )

    def _offer(self, eid, attrs):
        return Offer.objects.create(
            supplier=self.supplier, external_id=eid, sku=eid,
            name=eid, product=self.product, is_published=True,
            is_available=True, selling_price=Decimal("10.00"),
            category_raw="Uncategorized", attributes=attrs,
        )

    def test_rod_axes_infer_rods_profile(self):
        from apps.catalog.profiles import profile_for

        offer = self._offer("I-1", {"axes": {
            "length": "3.0m", "sections": "3", "casting_weight": "10-30g"}})
        self.assertEqual(profile_for(_offer_context(offer)).code, "rods")
        # 'length' suinferuotame rods kontekste -> rod-length-m option
        self._offer("I-2", {"axes": {
            "length": "3.6m", "sections": "3", "casting_weight": "10-30g"}})
        payload = build_catalog_payload()
        options = {v["code"]: v["options"]
                   for v in payload["items"][0]["variants"]}
        self.assertEqual(options["I-1"], {"rod-length-m": "3-m"})
        self.assertEqual(options["I-2"], {"rod-length-m": "3-6-m"})

    def test_reel_axes_infer_reels_profile(self):
        from apps.catalog.profiles import profile_for

        offer = self._offer("I-1", {"axes": {
            "reel_size": "4000", "gear_ratio": "5.1:1"}})
        self.assertEqual(profile_for(_offer_context(offer)).code, "reels")

    def test_no_false_inference(self):
        from apps.catalog.profiles import profile_for

        offer = self._offer("I-1", {"axes": {
            "size": "85mm", "color": "red"}})
        self.assertEqual(profile_for(_offer_context(offer)).code, "generic")


class NonGenericCompositeTests(TestCase):
    """A: kompozitinė reikšmė NE-generic ašyje – Shimano
    'color: 2740m0,15mm Aqua Green' -> line_length + line_diameter,
    liekana 'Aqua Green' lieka 'color' ašyje."""

    def setUp(self):
        Language.objects.create(code="en", name="English", is_base=True)
        self.supplier = Supplier.objects.create(
            code="shimano", name="Shimano", vat_rate=Decimal("0.21"),
            selling_price_includes_vat=True,
        )
        self.category = Category.objects.create(
            name="Braided lines", slug="braided-lines-ng"
        )
        self.product = Product.objects.create(
            sku="sh-PP-1", name="PowerPro Super 8 Slick", is_active=True,
            category=self.category,
        )

    def _offer(self, eid, attrs):
        return Offer.objects.create(
            supplier=self.supplier, external_id=eid, sku=eid,
            name=f"PowerPro {eid}", product=self.product,
            is_published=True, is_available=True,
            selling_price=Decimal("10.00"), attributes=attrs,
        )

    def test_composite_color_splits_and_keeps_leftover(self):
        self._offer("V-1", {"axes": {
            "color": "2740m0,15mm Aqua Green", "strength": "10kg"}})
        self._offer("V-2", {"axes": {
            "color": "2740m0,19mm Aqua Green", "strength": "15kg"}})
        payload = build_catalog_payload()
        item = payload["items"][0]
        options = {v["code"]: v["options"] for v in item["variants"]}
        self.assertEqual(options["V-1"], {"line-diameter": "0-15-mm"})
        self.assertEqual(options["V-2"], {"line-diameter": "0-19-mm"})
        # 'color' liekana yra tik 'Aqua Green' – be '2740m0,15mm'.
        feature_map = {
            f["code"]: f["value_translations"]["en"] for f in item["features"]
        }
        self.assertEqual(feature_map["color"], "Aqua Green")
        self.assertEqual(feature_map["line-length"], "2740 m")
        vf = {
            v["code"]: {f["code"]: f["value_translations"]["en"]
                        for f in v["features"]}
            for v in item["variants"]
        }
        self.assertEqual(vf["V-1"].get("line-strength"), "10 kg")
        self.assertEqual(vf["V-2"].get("line-strength"), "15 kg")

    def test_plain_color_not_split(self):
        # 'Black/Grey' – produced tuščias -> gate nepraleidžia.
        self._offer("V-1", {"axes": {
            "color": "Black/Grey", "strength": "10kg"}})
        self._offer("V-2", {"axes": {
            "color": "Black/Green", "strength": "15kg"}})
        payload = build_catalog_payload()
        item = payload["items"][0]
        options = {v["code"]: v["options"] for v in item["variants"]}
        self.assertEqual(options["V-1"], {"color": "black-grey"})
        self.assertEqual(options["V-2"], {"color": "black-green"})
        feature_codes = {f["code"] for f in item["features"]}
        self.assertNotIn("line-diameter", feature_codes)


class LeftoverRoutingTests(TestCase):
    """B: generic 'size' liekanų/neskaldytų reikšmių nukreipimas per
    profile.leftover_axis (lures->color, bait->flavour, kita->model)."""

    def setUp(self):
        Language.objects.create(code="en", name="English", is_base=True)
        self.supplier = Supplier.objects.create(
            code="moss", name="MOSS", vat_rate=Decimal("0.21"),
            selling_price_includes_vat=True,
        )

    def _product(self, category_name):
        category = Category.objects.create(
            name=category_name, slug=category_name.lower().replace(" ", "-")
        )
        return Product.objects.create(
            sku=f"moss-{category.slug}", name="Item", is_active=True,
            category=category,
        )

    def _offer(self, product, eid, name, size_raw="", attrs=None):
        return Offer.objects.create(
            supplier=self.supplier, external_id=eid, sku=eid, name=name,
            product=product, is_published=True, is_available=True,
            selling_price=Decimal("10.00"), size_raw=size_raw,
            attributes=attrs or {},
        )

    def test_lures_whole_size_goes_to_color(self):
        product = self._product("Lures")
        self._offer(product, "W-1", "Worm A", size_raw="PERCHY")
        self._offer(product, "W-2", "Worm B", size_raw="BN")
        payload = build_catalog_payload()
        options = {
            v["code"]: v["options"] for v in payload["items"][0]["variants"]
        }
        self.assertEqual(options["W-1"], {"color": "perchy"})
        self.assertEqual(options["W-2"], {"color": "bn"})

    def test_lures_size_word_stays_size(self):
        product = self._product("Lures")
        self._offer(product, "W-1", "Worm A", size_raw="M")
        self._offer(product, "W-2", "Worm B", size_raw="XL")
        payload = build_catalog_payload()
        options = {
            v["code"]: v["options"] for v in payload["items"][0]["variants"]
        }
        self.assertEqual(options["W-1"], {"size": "m"})
        self.assertEqual(options["W-2"], {"size": "xl"})

    def test_bait_whole_size_goes_to_flavour(self):
        product = self._product("Boilies")
        self._offer(product, "B-1", "Boilie A", size_raw="Corn-Ananas")
        self._offer(product, "B-2", "Boilie B", size_raw="Honey-Peach")
        payload = build_catalog_payload()
        options = {
            v["code"]: v["options"] for v in payload["items"][0]["variants"]
        }
        self.assertEqual(options["B-1"], {"flavour": "corn-ananas"})
        self.assertEqual(options["B-2"], {"flavour": "honey-peach"})

    def test_other_profile_non_size_leftover_to_model(self):
        product = self._product("Braided lines")
        self._offer(product, "L-1", "Line A", size_raw="0,10mm/PowerPro")
        self._offer(product, "L-2", "Line B", size_raw="0,12mm/PowerPro")
        payload = build_catalog_payload()
        item = payload["items"][0]
        options = {v["code"]: v["options"] for v in item["variants"]}
        self.assertEqual(options["L-1"], {"line-diameter": "0-1-mm"})
        self.assertEqual(options["L-2"], {"line-diameter": "0-12-mm"})
        # 'PowerPro' – liekana be skaitmenų ir ne dydžio žodis -> 'model'
        # (vienoda visame produkte -> product feature, ne 'size').
        feature_map = {
            f["code"]: f["value_translations"]["en"] for f in item["features"]
        }
        self.assertEqual(feature_map.get("model"), "PowerPro")
        self.assertNotIn("size", feature_map)

    def test_digit_leftover_stays_size(self):
        product = self._product("Braided lines")
        self._offer(product, "L-1", "Line A", size_raw="0,10mm/PE4")
        self._offer(product, "L-2", "Line B", size_raw="0,12mm/PE8")
        payload = build_catalog_payload()
        item = payload["items"][0]
        # 'PE4'/'PE8' turi skaitmenis -> lieka 'size' -> variant features.
        vf = {
            v["code"]: {f["code"]: f["value_translations"]["en"]
                        for f in v["features"]}
            for v in item["variants"]
        }
        self.assertEqual(vf["L-1"].get("size"), "PE4")
        self.assertNotIn("model", vf["L-1"])


class LineNameAxesTests(TestCase):
    """C: mm/m/lb iš offer+product pavadinimo -> lines ašys; pliki
    vienetai ('m') – tuščios reikšmės."""

    def setUp(self):
        Language.objects.create(code="en", name="English", is_base=True)
        self.supplier = Supplier.objects.create(
            code="korda", name="Korda", vat_rate=Decimal("0.21"),
            selling_price_includes_vat=True,
        )
        self.category = Category.objects.create(
            name="Braided lines", slug="braided-lines-c"
        )
        self.product = Product.objects.create(
            sku="korda-line", name="Line product", is_active=True,
            category=self.category,
        )

    def _offer(self, eid, name, attrs):
        return Offer.objects.create(
            supplier=self.supplier, external_id=eid, sku=eid, name=name,
            product=self.product, is_published=True, is_available=True,
            selling_price=Decimal("10.00"), attributes=attrs,
        )

    def test_apex_strength_length_from_name(self):
        self._offer(
            "APEXB50", "Korda Apex Braided Mainline 50lb 1200 m",
            {"axes": {"size": "m"}},
        )
        payload = build_catalog_payload()
        item = payload["items"][0]
        feature_map = {
            f["code"]: f["value_translations"]["en"] for f in item["features"]
        }
        # 'm' – plikas vienetas -> 'size' dingsta; mm/lb iš vardo.
        self.assertNotIn("size", feature_map)
        self.assertEqual(feature_map["line-strength"], "22,68 kg (50 lb)")
        self.assertEqual(feature_map["line-length"], "1200 m")

    def test_guru_pulse_diameter_from_name(self):
        self._offer(
            "GP901", "Guru Pulse 9 Braid (0.08mm) 150m",
            {"axes": {"variantas": "150m"}},
        )
        self._offer(
            "GP902", "Guru Pulse 9 Braid (0.10mm) 150m",
            {"axes": {"variantas": "150m"}},
        )
        self._offer(
            "GP903", "Guru Pulse 9 Braid (0.12mm) 150m",
            {"axes": {"variantas": "150m"}},
        )
        payload = build_catalog_payload()
        item = payload["items"][0]
        options = {v["code"]: v["options"] for v in item["variants"]}
        self.assertEqual(options["GP901"], {"line-diameter": "0-08-mm"})
        self.assertEqual(options["GP902"], {"line-diameter": "0-1-mm"})
        self.assertEqual(options["GP903"], {"line-diameter": "0-12-mm"})
        names = {v["code"]: v["name"] for v in item["variants"]}
        # 'variantas: 150m' – ne kompozitinė -> lieka varianto vardas.
        self.assertEqual(names["GP901"], "150m")

    def test_existing_axis_wins_over_name(self):
        self._offer(
            "X-1", "Guru Pulse 9 Braid (0.08mm) 150m",
            {"axes": {"line_diameter": "0.30mm"}},
        )
        self._offer(
            "X-2", "Guru Pulse 9 Braid (0.10mm) 150m",
            {"axes": {"line_diameter": "0.35mm"}},
        )
        payload = build_catalog_payload()
        options = {
            v["code"]: v["options"] for v in payload["items"][0]["variants"]
        }
        # Esama ašis laimi prieš vardo ištraukimą.
        self.assertEqual(options["X-1"], {"line-diameter": "0-3-mm"})
        self.assertEqual(options["X-2"], {"line-diameter": "0-35-mm"})

    def test_clothing_size_m_not_dropped(self):
        # 'M' (uppercase) yra dydis – tik plikas 'm' vienetas tuščias.
        category = Category.objects.create(name="Hoodies", slug="hoodies")
        product = Product.objects.create(
            sku="korda-hoodie", name="Hoodie", is_active=True,
            category=category,
        )
        for eid, size in (("H-1", "M"), ("H-2", "L")):
            Offer.objects.create(
                supplier=self.supplier, external_id=eid, sku=eid,
                name="Team Hoodie", product=product, is_published=True,
                is_available=True, selling_price=Decimal("10.00"),
                attributes={"axes": {"size": size}},
            )
        payload = build_catalog_payload()
        item = next(i for i in payload["items"] if i["code"] == "korda-hoodie")
        options = {v["code"]: v["options"] for v in item["variants"]}
        self.assertEqual(options["H-1"], {"size": "m"})
        self.assertEqual(options["H-2"], {"size": "l"})
