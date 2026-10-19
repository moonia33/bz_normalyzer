from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import Brand, Category, Product, ProductText
from apps.catalog_export.services import (
    build_catalog_payload,
    build_stock_payload,
    payload_sha256,
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
        self.assertEqual(payload["format_version"], 1)
        self.assertEqual(payload["source"], "normalyzer")
        self.assertEqual(payload["mode"], "upsert")
        self.assertEqual(len(payload["items"]), 1)

        item = payload["items"][0]
        self.assertEqual(item["code"], "fox-AAA-1")
        self.assertEqual(item["ean"], "1111111111111")
        self.assertEqual(item["brand_code"], "fox-rage")
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

    def test_payload_hash_stable(self):
        p1 = build_catalog_payload()
        p2 = build_catalog_payload()
        p1.pop("generated_at")
        p2.pop("generated_at")
        self.assertEqual(payload_sha256(p1), payload_sha256(p2))
