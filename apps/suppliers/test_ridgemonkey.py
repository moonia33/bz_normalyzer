from decimal import Decimal
from types import SimpleNamespace

import pytest
from lxml import html as lxml_html

from apps.suppliers.adapters.ridgemonkey import (
    RidgeMonkeyAdapter,
    _axes_from_label,
    _classify_row,
    _common_prefix,
)


def _adapter():
    return RidgeMonkeyAdapter(
        SimpleNamespace(config={}, file=None, language=None)
    )


class TestClassifyRow:
    def test_single_item(self):
        out = _classify_row("APEarel Classic Joggers S - Single Item (RMC231)")
        assert out["row_type"] == "single_item"
        assert out["art_no"] == "RMC231"
        assert out["base_name"] == "APEarel Classic Joggers S"

    def test_singe_item_typo(self):
        out = _classify_row("Foo M - Singe Item (RM1)")
        assert out["row_type"] == "single_item"

    def test_trade_pack(self):
        out = _classify_row("Ape-X Size 4 - Trade Pack of 5 (RMT286)")
        assert out["row_type"] == "trade_pack"
        assert out["trade_pack_qty"] == 5
        assert out["art_no"] == "RMT286"

    def test_plain_art(self):
        out = _classify_row("Bivvy (RM980)")
        assert out["row_type"] == "single_item"
        assert out["art_no"] == "RM980"
        assert out["base_name"] == "Bivvy"


class TestAxes:
    def test_letter_size(self):
        assert _axes_from_label("S") == {"size": "S"}

    def test_bare_number(self):
        assert _axes_from_label("4") == {"size": "4"}

    def test_color(self):
        assert _axes_from_label("Weed Green") == {"color": "Weed Green"}

    def test_measure_fallback(self):
        assert _axes_from_label("300m") == {"variantas": "300m"}

    def test_empty(self):
        assert _axes_from_label("") == {}


class TestPrefix:
    def test_common(self):
        assert _common_prefix(["Ape-X Barbed Size 4", "Ape-X Barbed Size 6"]) == "Ape-X Barbed Size "

    def test_single(self):
        assert _common_prefix([]) == ""


_PAGE_HTML = """
<html><body>
<div class="breadcrumb"><ul>
  <li><span itemprop="name">Home</span></li>
  <li><span itemprop="name">Terminal Tackle</span></li>
  <li><span itemprop="name">Hooks</span></li>
</ul></div>
<img id="main-product-img-1363" src="http://cdn/img.jpg" data-fullsize="/big.jpg"/>
<h1>Ape-X Beaked Point Hooks Barbed (RMT)</h1>
<div class="product-details-form">
<div class="product-variant-list"><table>
<tr><td>Ape-X Barbed Size 4 - Trade Pack of 5 (RMT286)</td>
    <td>RMT AXBP B45</td>
    <td><img src="/barcode/5056210616185"/></td>
    <td><span class="stock stock-False">Out of Stock</span></td>
    <td>19.000 (EUR) excl tax</td>
    <td><button data-productid="1397"></button>
        <script>addproducttocart/details/1397/</script></td></tr>
<tr><td>Ape-X Barbed Size 6 - Trade Pack of 5 (RMT287)</td>
    <td>RMT AXBP B46</td>
    <td><img src="/barcode/5056210616215"/></td>
    <td><span class="stock stock-True">In Stock</span></td>
    <td>19.000 (EUR) excl tax</td>
    <td>addproducttocart/details/1398/</td></tr>
</table></div></div>
<div class="full-description"><p>Desc</p></div>
</body></html>
"""


class TestParsePage:
    def test_pack_only_becomes_units(self):
        items = _adapter()._parse_product_page(
            _PAGE_HTML, "https://b2b.ridgemonkey.co.uk/ape-x"
        )
        assert len(items) == 2
        r = items[0]
        assert r["art_no"] == "RMT286"
        assert r["unit_basis"] == "pack_share"
        assert r["trade_pack_qty"] == 5
        assert Decimal(r["unit_price"]) == Decimal("3.8000")
        assert r["pack_price"] == "19.000"
        assert r["ean"] == "5056210616185"
        assert r["stock_status"] == "out_of_stock"
        assert r["category_path"] == ["Terminal Tackle", "Hooks"]
        assert r["variant_label"] == "4"
        assert r["base_name"] == "Ape-X Beaked Point Hooks Barbed"
        assert "https" in r["images"][0]

    def test_normalize_unit_price(self):
        item = _adapter().normalize(
            _adapter()._parse_product_page(
                _PAGE_HTML, "https://b2b.ridgemonkey.co.uk/ape-x"
            )[0]
        )
        assert item.external_id == "RMT286"
        assert item.purchase_price == Decimal("3.8000")
        assert item.extra["trade_pack_qty"] == 5
        assert item.stock == 0 and not item.is_available
        assert item.extra["axes"] if "axes" in item.extra else item.attributes == {
            "axes": {"size": "4"}
        }


class TestNormalizeFixes:
    URL = "https://b2b.ridgemonkey.co.uk/ape-x"

    def test_pack_share_unit_has_no_carton_ean(self):
        row = _adapter()._parse_product_page(_PAGE_HTML, self.URL)[0]
        item = _adapter().normalize(row)
        assert item.ean == ""
        assert item.extra["pack_ean"] == "5056210616185"
        assert item.extra["sub_category_raw"] == "Hooks"

    def test_root_only_category_has_no_sub(self):
        row = dict(
            _adapter()._parse_product_page(_PAGE_HTML, self.URL)[0],
            category_path=["Bivvies"],
        )
        item = _adapter().normalize(row)
        assert item.category_path == "Bivvies"
        assert "sub_category_raw" not in item.extra

    def test_multiword_colour(self):
        assert _axes_from_label("Weed Green") == {"color": "Weed Green"}
