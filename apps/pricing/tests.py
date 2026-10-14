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
