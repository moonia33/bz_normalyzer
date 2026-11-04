from decimal import Decimal

from django.test import TestCase

from apps.ingestion.models import Offer
from apps.pricing.engine import compute_selling_price
from apps.pricing.models import PriceAction, PriceRule, RoundingMode
from apps.suppliers.models import Supplier


class PriceEngineTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(code="acme", name="Acme")
        self.offer = Offer.objects.create(
            supplier=self.supplier,
            external_id="1",
            name="Test",
            purchase_price=Decimal("10.00"),
        )

    def test_percent_markup_then_end_99(self):
        PriceRule.objects.create(
            name="30%",
            priority=10,
            action=PriceAction.MARKUP_PERCENT,
            value=Decimal("30"),
        )
        PriceRule.objects.create(
            name=".99",
            priority=20,
            action=PriceAction.ROUND,
            rounding=RoundingMode.END_99,
        )
        price = compute_selling_price(self.offer)
        self.assertEqual(price, Decimal("12.99"))

    def test_supplier_scope(self):
        other = Supplier.objects.create(code="other", name="Other")
        PriceRule.objects.create(
            name="only other",
            priority=10,
            supplier=other,
            action=PriceAction.MARKUP_PERCENT,
            value=Decimal("100"),
        )
        price = compute_selling_price(self.offer)
        self.assertEqual(price, Decimal("10.00"))


class OnlyWithoutRrpRuleTests(TestCase):
    def test_rule_skips_offers_with_rrp(self):
        supplier = Supplier.objects.create(code="oc", name="Oc")
        PriceRule.objects.create(
            name="x2",
            supplier=supplier,
            action=PriceAction.MARKUP_PERCENT,
            value=Decimal("100"),
            rounding=RoundingMode.END_95,
            only_without_rrp=True,
        )
        plain = Offer.objects.create(
            supplier=supplier, external_id="a", name="A", purchase_price=Decimal("10.00")
        )
        with_rrp = Offer.objects.create(
            supplier=supplier,
            external_id="b",
            name="B",
            purchase_price=Decimal("10.00"),
            rrp_price=Decimal("24.99"),
        )
        self.assertEqual(compute_selling_price(plain), Decimal("19.95"))
        self.assertEqual(compute_selling_price(with_rrp), Decimal("24.99"))
        zero_rrp = Offer.objects.create(
            supplier=supplier,
            external_id="c",
            name="C",
            purchase_price=Decimal("10.00"),
            rrp_price=Decimal("0"),
        )
        self.assertEqual(compute_selling_price(zero_rrp), Decimal("19.95"))
