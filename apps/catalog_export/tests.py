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
            attributes={"specs": {"Unit Height": "10"}},
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
        self.assertEqual(item["features"], [{"code": "unit-height", "value": "10"}])
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
        self.assertEqual(variants[0]["options"], {"size": "Medium"})
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

    def test_payload_hash_stable(self):
        p1 = build_catalog_payload()
        p2 = build_catalog_payload()
        p1.pop("generated_at")
        p2.pop("generated_at")
        self.assertEqual(payload_sha256(p1), payload_sha256(p2))
