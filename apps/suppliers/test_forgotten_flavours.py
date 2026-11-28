import json
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


class _Resp:
    def __init__(self, status, body="", headers=None):
        self.status_code = status
        self.text = body
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


class _Session:
    def __init__(self, handler):
        self.headers = {}
        self.calls = []
        self._handler = handler

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self._handler(url)


def _fetch_with(monkeypatch, handler):
    from apps.suppliers.adapters import forgotten_flavours as ff

    session = _Session(handler)
    monkeypatch.setattr(ff.requests, "Session", lambda: session)
    monkeypatch.setattr(ff.time, "sleep", lambda s: None)
    adapter = _adapter({"currency": "GBP", "retries": 5, "request_delay": 0})
    adapter.feed.role = "stock"
    return ff, adapter, session


class TestFetchBlocked:
    def test_cloudflare_challenge_fails_fast(self, monkeypatch):
        products = json.dumps({"products": [_product(), {**_product(), "handle": "b"}]})

        def handler(url):
            if "products.json" in url:
                return _Resp(200, products)
            return _Resp(403, "<html>Verifying", {"cf-mitigated": "challenge"})

        ff, adapter, session = _fetch_with(monkeypatch, handler)
        import pytest

        with pytest.raises(ff.SourceBlockedError, match="Cloudflare"):
            adapter.fetch()
        assert len([u for u in session.calls if u.endswith(".js")]) == 1

    def test_consecutive_product_errors_abort(self, monkeypatch):
        many = [{**_product(), "handle": f"h{i}"} for i in range(30)]

        def handler(url):
            if "products.json" in url:
                return _Resp(200, json.dumps({"products": many}))
            return _Resp(500, "oops")

        ff, adapter, session = _fetch_with(monkeypatch, handler)
        import pytest

        with pytest.raises(ff.AdapterError, match="iš eilės"):
            adapter.fetch()
        product_calls = [u for u in session.calls if u.endswith(".js")]
        assert len(product_calls) == ff.MAX_CONSECUTIVE_ERRORS * 6


class TestFetchBudget:
    def test_fetch_time_budget_aborts(self, monkeypatch):
        many = [{**_product(), "handle": f"h{i}"} for i in range(30)]
        js = json.dumps({"variants": [{"id": 11, "available": True}]})

        def handler(url):
            if "products.json" in url:
                return _Resp(200, json.dumps({"products": many}))
            return _Resp(200, js)

        ff, adapter, session = _fetch_with(monkeypatch, handler)
        clock = iter(range(0, 100000, 600))  # kiekvienas kvietimas +10 min.
        monkeypatch.setattr(ff.time, "monotonic", lambda: next(clock))
        import pytest

        with pytest.raises(ff.AdapterError, match="viršijo 45 min"):
            adapter.fetch()
        assert len([u for u in session.calls if u.endswith(".js")]) < 10

    def test_retry_after_wait_is_capped(self, monkeypatch):
        from apps.suppliers.adapters import forgotten_flavours as ff

        waits = []
        responses = iter([_Resp(429, "slow", {"Retry-After": "3600"}), _Resp(200, '{"ok": 1}')])
        session = _Session(lambda url: next(responses))
        monkeypatch.setattr(ff.time, "sleep", waits.append)
        assert _adapter()._get_json(session, "https://x/y.js", 30, 3) == {"ok": 1}
        assert waits == [ff.MAX_RETRY_WAIT_SECONDS]
