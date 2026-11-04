import json
from types import SimpleNamespace

from apps.suppliers.adapters.holdcarp import (
    HoldCarpAdapter,
    _fragment_attrs,
    _page_breadcrumbs,
)


def _adapter(config=None):
    return HoldCarpAdapter(
        SimpleNamespace(config=config or {}, file=None, language=None)
    )


def _combo(id_attr="68", fragment="#/15-colour-green"):
    return {
        "id_product": "24",
        "id_product_attribute": id_attr,
        "ean": "8588007219486",
        "category": "Markers",
        "product_url": "https://holdcarp.sk/gb/markers/24-x.html",
        "fragment": fragment,
    }


_PRODUCT = {
    "id": 24,
    "name": "HOLDCARP LED Light",
    "reference": "50695023",
    "price": "€24.90",
    "quantity": -7,
    "description": "<p>LED light.</p>",
    "attributes": {
        "2": {"id_attribute": "15", "name": "Green", "group": "Colour",
              "reference": "50695022", "ean13": ""},
    },
}


class TestFragments:
    def test_attrs(self):
        assert _fragment_attrs("#/18-colour-pink/26-dimension-4m") == {
            "colour": "pink", "dimension": "4m",
        }

    def test_empty(self):
        assert _fragment_attrs("") == {}


class TestMakeItem:
    def test_combo_reference(self):
        item = _adapter()._make_item(
            _combo(), _PRODUCT, "HOLDCARP LED Light", "<p>LED light.</p>",
            ["Markers"], ["https://img/1.jpg"], "",
        )
        assert item["external_id"] == "50695022"
        assert item["axes_raw"] == {"colour": "Green"}
        assert item["ean"] == "8588007219486"
        assert item["quantity"] == -7

    def test_normalize(self):
        item = _adapter()._make_item(
            _combo(), _PRODUCT, "HOLDCARP LED Light", "<p>LED light.</p>",
            ["Markers"], ["https://img/1.jpg"], "",
        )
        n = _adapter().normalize(item)
        assert n.external_id == "50695022"
        assert n.sku == "50695022"
        assert n.attributes == {"axes": {"color": "Green"}}
        assert n.purchase_price is None
        assert n.extra["rrp_price"] == "24.90"
        assert n.stock == 0 and not n.is_available
        assert n.category_path == "Markers"

    def test_preorder_stock(self):
        product = dict(_PRODUCT, _preorder=True)
        item = _adapter()._make_item(
            _combo(), product, "X", "", ["Markers"], [], "",
        )
        n = _adapter().normalize(item)
        assert n.stock == 10 and n.is_available
        assert n.extra["stock_text"] == "preorder"

    def test_parse_dedupe(self):
        item = _adapter()._make_item(
            _combo(), _PRODUCT, "X", "", ["Markers"], [], "",
        )
        payload = json.dumps({"items": [item, item]}).encode()
        rows = list(_adapter().parse(payload))
        assert len(rows) == 1


class TestBreadcrumbJson:
    def test_links(self):
        text = '"breadcrumb":{"links":[{"title":"Home","url":"a"},{"title":"Markers","url":"b"},{"title":"LED Light","url":"c"}]}'
        assert _page_breadcrumbs(text) == ["Markers", "LED Light"]


class TestCombinationFixes:
    def test_blank_combo_reference_keeps_variants_apart(self):
        product = json.loads(json.dumps(_PRODUCT))
        product["attributes"]["2"]["reference"] = ""
        a = _adapter()._make_item(_combo("68"), product, "X", "", ["Markers"], [], "")
        b = _adapter()._make_item(_combo("69"), product, "X", "", ["Markers"], [], "")
        assert a["external_id"] == "50695023-68"
        assert b["external_id"] == "50695023-69"

    def test_malformed_refresh_returns_none(self):
        adapter = _adapter()
        adapter._get = lambda *a, **k: SimpleNamespace(
            json=lambda: {"product_details": "<div>nothing</div>"}
        )
        assert adapter._combo_data(None, "u", "24", "68", 1, 0) is None

    def test_fetch_errors_counted(self):
        adapter = _adapter()
        payload = json.dumps({"items": [], "errors": [{"id_product": "1", "error": "x"}]})
        list(adapter.parse(payload.encode()))
        assert adapter.fetch_errors == 1
