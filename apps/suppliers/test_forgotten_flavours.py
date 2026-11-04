import json
from decimal import Decimal
from types import SimpleNamespace

from apps.suppliers.adapters.forgotten_flavours import (
    ForgottenFlavoursAdapter,
    _axes_from_options,
)


def _adapter(config=None):
    return ForgottenFlavoursAdapter(
        SimpleNamespace(config=config or {"currency": "GBP"}, file=None, language=None)
    )


def _product():
    return {
        "id": 1,
        "title": "Ester Explosion pop-ups",
        "handle": "ester-explosion-pop-ups",
        "body_html": "<p>Pure filth.</p>",
        "vendor": "Forgotten Flavours",
        "product_type": "High attract hookbaits Forgotten Flavours",
        "tags": "hookbait, pop-ups",
        "images": [{"src": "https://cdn/x.jpg?v=1"}],
        "variants": [
            {"id": 11, "title": "12mm", "sku": "FF-PU-EE12", "price": "14.99",
             "compare_at_price": None, "grams": 50, "barcode": ""},
            {"id": 12, "title": "15mm", "sku": "FF-PU-EE15", "price": "14.99",
             "compare_at_price": None, "grams": 50, "barcode": ""},
        ],
    }


class TestAxes:
    def test_mm_is_size(self):
        assert _axes_from_options("12mm") == {"size": "12mm"}

    def test_word_size(self):
        assert _axes_from_options("Medium") == {"size": "Medium"}

    def test_default_title_empty(self):
        assert _axes_from_options("Default Title") == {}

    def test_slash_combo(self):
        assert _axes_from_options("Olive / XL") == {"size": "XL", "color": "Olive"}


class TestItems:
    def test_rows(self):
        items = _adapter()._product_items(_product(), "https://x.com", None)
        assert len(items) == 2
        r = items[0]
        assert r["external_id"] == "11"
        assert r["variant_label"] == "12mm"
        assert r["product_group"] == "ester-explosion-pop-ups"
        assert r["images"] == ["https://cdn/x.jpg"]
        assert r["tags"] == ["hookbait", "pop-ups"]

    def test_normalize(self):
        items = _adapter()._product_items(_product(), "https://x.com", None)
        n = _adapter().normalize(items[0])
        assert n.external_id == "11"
        # 'FF-' nuimamas – shop kodas bus FR.PU-EE12
        assert n.sku == "PU-EE12"
        assert n.purchase_price is None
        assert n.extra["rrp_price"] == "14.99"
        assert n.currency == "EUR"
        assert n.attributes == {"axes": {"size": "12mm"}}
        assert n.is_available and n.stock == 10

    def test_parse_dedupe(self):
        items = _adapter()._product_items(_product(), "https://x.com", None)
        payload = json.dumps({"items": items + items[:1]}).encode()
        rows = list(_adapter().parse(payload))
        assert len(rows) == 2


class TestCurrencyAndErrors:
    def test_gbp_converted_to_eur_and_fetch_errors_counted(self):
        adapter = _adapter()
        rows = _adapter()._product_items(_product(), "https://x.com", None)
        payload = json.dumps(
            {"items": rows, "errors": [{"handle": "x", "error": "429"}], "rate_per_eur": "0.85"}
        ).encode()
        n = adapter.normalize(next(iter(adapter.parse(payload))))
        assert n.extra["rrp_price"] == "17.64"
        assert n.currency == "EUR"
        assert adapter.fetch_errors == 1
