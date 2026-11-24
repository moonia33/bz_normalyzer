from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from apps.ingestion.models import Offer
from apps.suppliers import promotions
from apps.suppliers.models import Supplier, SupplierPromotion

ORDER_HTML = """
<div id="basket_token" style="display:none;">abc123</div>
<div>Offer applies to discount reference <b><a href="javascript:void(0);" class="getDiscLink" id="FC"><b><em>FC</em></b></a></b> - 10% FC Discount &nbsp; <b>Offer</b> = 10 % discount<br/></div>
<div>Offer applies to discount reference <b><a href="javascript:void(0);" class="getDiscLink" id="CLEAR50"><b><em>CLEAR50</em></b></a></b> - 50% CLEARANCE &nbsp; <b>Offer</b> = 50 % discount<br/></div>
"""
REF_HTML = """<b>Products available for discount code CLEAR50:</b><br/>CLEAR50 Discount reference applies to product: <b><a class="addDiscoProd" data-link="NPR420" href="javascript:void(0);"><b>NPR420</b></a></b> - Fox Rage Heated Gilet XL<br/>
CLEAR50 Discount reference applies to product: <b><a class="addDiscoProd" data-link="GRD196" href="javascript:void(0);"><b>GRD196</b></a></b> - Ethos XRS &amp; Feeder<br/>
"""


class ParseTests(TestCase):
    def test_offer_refs(self):
        self.assertEqual(promotions.parse_fox_offer_refs(ORDER_HTML), {"FC": 10, "CLEAR50": 50})

    def test_ref_items(self):
        self.assertEqual(
            promotions.parse_fox_ref_items(REF_HTML),
            [("NPR420", "Fox Rage Heated Gilet XL"), ("GRD196", "Ethos XRS & Feeder")],
        )


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PIPELINE_ALERT_EMAILS=["ops@example.com"],
    PROMOTION_ALERT_EMAILS=["admin@example.com", "info@example.com"],
)
class SyncTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(code="fox", name="FOX", sku_prefix="FX.")
        self.a = self._offer("A1", stock=10)
        self.b = self._offer("B1", stock=0, is_available=False)
        self.c = self._offer("C1", stock=10)

    def _offer(self, code, **kw):
        defaults = {"stock": 10, "is_available": True, "purchase_price": Decimal("10"), "rrp_price": Decimal("25")}
        defaults.update(kw)
        return Offer.objects.create(
            supplier=self.supplier, external_id=code, sku=code, name=f"Prekė {code}", **defaults
        )

    def _sync(self, listed):
        with patch.dict(promotions.SOURCES, {"fox": lambda: listed}):
            return promotions.sync_promotions(self.supplier)

    def _listed(self, *codes, pct=50, ref="CLEAR50"):
        return {c: {"reference": ref, "discount_percent": pct, "name": c} for c in codes}

    def test_new_promotions_apply_discount_and_notify_only_in_stock(self):
        stats = self._sync(self._listed("A1", "B1", "ZZ9"))
        self.assertEqual(stats["new"], 1)
        self.assertEqual(stats["active"], 2)  # ZZ9 – ne mūsų kataloge
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.c.refresh_from_db()
        self.assertEqual((self.a.discount_percent, self.b.discount_percent, self.c.discount_percent), (50, 50, None))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["admin@example.com", "info@example.com"])
        self.assertIn("1 naujos", mail.outbox[0].subject)
        self.assertIn("FX.A1", mail.outbox[0].body)
        self.assertNotIn("FX.B1", mail.outbox[0].body)

    def test_unchanged_list_sends_nothing(self):
        self._sync(self._listed("A1"))
        self._sync(self._listed("A1"))
        self.assertEqual(len(mail.outbox), 1)

    def test_out_of_stock_and_removed_are_ended(self):
        self._sync(self._listed("A1", "C1"))
        Offer.objects.filter(pk=self.a.pk).update(stock=0, is_available=False)
        stats = self._sync(self._listed("A1"))
        self.assertEqual((stats["new"], stats["ended"]), (0, 2))
        body = mail.outbox[-1].body
        self.assertIn("baigėsi likutis", body)
        self.assertIn("išimta iš išpardavimo", body)
        self.c.refresh_from_db()
        self.assertIsNone(self.c.discount_percent)
        self.assertFalse(SupplierPromotion.objects.get(code="C1").is_active)

    def test_back_in_stock_is_new(self):
        self._sync(self._listed("B1"))
        Offer.objects.filter(pk=self.b.pk).update(stock=10, is_available=True)
        stats = self._sync(self._listed("B1"))
        self.assertEqual(stats["new"], 1)
        self.assertIn("vėl yra likutis", mail.outbox[-1].body)

    def test_fetch_failure_keeps_discounts(self):
        self._sync(self._listed("A1"))

        def boom():
            raise promotions.PromotionError("prisijungti nepavyko")

        with patch.dict(promotions.SOURCES, {"fox": boom}):
            stats = promotions.sync_promotions(self.supplier)
            promotions.sync_promotions(self.supplier)
        self.assertIn("error", stats)
        self.a.refresh_from_db()
        self.assertEqual(self.a.discount_percent, 50)
        self.assertTrue(SupplierPromotion.objects.get(code="A1").is_active)
        failures = [m for m in mail.outbox if "nepavyko" in m.subject]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].to, ["ops@example.com"])

    def test_apply_restores_discount_after_full_import(self):
        self._sync(self._listed("A1"))
        Offer.objects.filter(pk=self.a.pk).update(discount_percent=None)
        self.assertEqual(promotions.apply_discounts(self.supplier), 1)
        self.a.refresh_from_db()
        self.assertEqual(self.a.discount_percent, 50)

    def test_supplier_without_source_untouched(self):
        other = Supplier.objects.create(code="albatros", name="Albatros")
        offer = Offer.objects.create(supplier=other, external_id="X", sku="X", name="X", discount_percent=30)
        self.assertIsNone(promotions.sync_promotions(other))
        self.assertEqual(promotions.apply_discounts(other), 0)
        offer.refresh_from_db()
        self.assertEqual(offer.discount_percent, 30)
