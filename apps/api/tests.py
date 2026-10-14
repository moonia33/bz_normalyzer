from django.test import TestCase
from ninja.testing import TestClient

from apps.api.api import api
from apps.ingestion.models import Offer
from apps.suppliers.models import Supplier


class ApiTests(TestCase):
    def setUp(self):
        self.client = TestClient(api)
        self.headers = {"X-API-Key": "dev-api-key"}
        supplier = Supplier.objects.create(code="demo", name="Demo")
        Offer.objects.create(
            supplier=supplier,
            external_id="1",
            name="Publikuota",
            is_available=True,
            is_published=True,
        )
        Offer.objects.create(
            supplier=supplier,
            external_id="2",
            name="Juodraštis",
            is_available=True,
            is_published=False,
        )

    def test_rejects_missing_key(self):
        response = self.client.get("/offers/")
        self.assertEqual(response.status_code, 401)

    def test_lists_only_published_offers(self):
        response = self.client.get("/offers/", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        names = [item["name"] for item in payload["items"]]
        self.assertEqual(names, ["Publikuota"])
