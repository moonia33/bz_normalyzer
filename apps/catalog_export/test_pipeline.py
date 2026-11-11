from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.test import TestCase

from apps.catalog.models import Category, Product, ProductText
from apps.catalog_export import pipeline
from apps.catalog_export.services import build_catalog_payload
from apps.catalog_export.models import ExportRun, PipelineRun
from apps.corecfg.models import Language
from apps.ingestion.models import Offer
from apps.suppliers.models import Supplier, SupplierSchedule

VILNIUS = ZoneInfo("Europe/Vilnius")


def _supplier(code="sup"):
    return Supplier.objects.create(
        code=code,
        name=code.upper(),
        sku_prefix="SP.",
        vat_rate=Decimal("0.21"),
        selling_price_includes_vat=True,
    )


class DueTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 2, 2, 30, tzinfo=VILNIUS)
        self.schedule = SupplierSchedule.objects.create(
            supplier=_supplier(),
            enabled=True,
            catalog_weekday=self.now.weekday(),
            catalog_hour=2,
            price_interval_hours=24,
            stock_interval_minutes=60,
        )

    def test_catalog_due_once_per_day_after_hour(self):
        self.assertIn("catalog", pipeline.due_stages(self.schedule, self.now))
        self.assertNotIn(
            "catalog", pipeline.due_stages(self.schedule, self.now - timedelta(hours=1))
        )
        self.schedule.last_catalog_at = self.now - timedelta(minutes=20)
        self.assertNotIn("catalog", pipeline.due_stages(self.schedule, self.now))
        self.assertNotIn(
            "catalog", pipeline.due_stages(self.schedule, self.now + timedelta(days=1))
        )

    def test_translating_phase_resumes_catalog(self):
        self.schedule.catalog_weekday = None
        self.schedule.catalog_phase = SupplierSchedule.Phase.TRANSLATING
        self.assertIn("catalog", pipeline.due_stages(self.schedule, self.now))

    def test_intervals_with_tolerance(self):
        self.schedule.last_prices_at = self.now - timedelta(hours=23, minutes=58)
        self.schedule.last_stock_at = self.now - timedelta(minutes=20)
        stages = pipeline.due_stages(self.schedule, self.now)
        self.assertIn("prices", stages)
        self.assertNotIn("stock", stages)
        self.schedule.stock_interval_minutes = 0
        self.schedule.last_stock_at = None
        self.assertNotIn("stock", pipeline.due_stages(self.schedule, self.now))

    def test_disabled(self):
        self.schedule.enabled = False
        self.assertEqual(pipeline.due_stages(self.schedule, self.now), [])


class PlanTests(TestCase):
    def setUp(self):
        Language.objects.create(code="en", name="English", is_base=True)
        self.lt = Language.objects.create(code="lt", name="Lietuvių")
        self.supplier = _supplier()
        self.schedule = SupplierSchedule.objects.create(supplier=self.supplier)
        self.category = Category.objects.create(name="Masalai", slug="masalai")

    def _offer(self, sku, product=None, **kw):
        defaults = dict(
            supplier=self.supplier,
            external_id=sku,
            sku=sku,
            name=f"Item {sku}",
            product=product,
            stock=5,
            is_available=True,
            is_published=True,
            selling_price=Decimal("12.10"),
            purchase_price=Decimal("5.00"),
        )
        defaults.update(kw)
        return Offer.objects.create(**defaults)

    def _product(self, sku, category=True, lt=True):
        product = Product.objects.create(
            sku=sku, name=f"Product {sku}", category=self.category if category else None
        )
        if lt:
            ProductText.objects.create(product=product, language=self.lt, name=f"Prekė {sku}")
        return product

    def _state(self, rows):
        return patch.object(pipeline, "fetch_variant_state", return_value=rows)

    def test_plan_stock_only_changed_and_known(self):
        self._offer("A1", stock=7)
        self._offer("A2", stock=3, is_discontinued=True)
        self._offer("A3", stock=1)
        self._offer("A4", stock=4)
        state = {
            "SP.A1": {"code": "SP.A1", "qty": 2},
            "SP.A2": {"code": "SP.A2", "qty": 3},
            "SP.A4": {"code": "SP.A4", "qty": 4},
        }
        with self._state(state) as mock_state:
            warehouse, items, stats = pipeline.plan_stock(self.supplier)
        self.assertEqual(warehouse, "sup")
        self.assertEqual(mock_state.call_args[0][1], "sup")
        self.assertEqual(items, [{"code": "SP.A1", "qty": 7}, {"code": "SP.A2", "qty": 0}])
        self.assertEqual(stats["in_shop"], 3)

    def test_plan_stock_sends_changed_offer_terms(self):
        self._offer("B1", stock=2, discount_percent=30)
        self._offer("B2", stock=2)
        self._offer("B3", stock=2, shop_variant_code="FX.123", discount_percent=10)
        state = {
            "SP.B1": {"code": "SP.B1", "qty": 2, "offer": None},
            "SP.B2": {"code": "SP.B2", "qty": 2,
                      "offer": {"price_net": None, "cost_net": "5.00", "discount_percent": None}},
            "FX.123": {"code": "FX.123", "qty": 2, "offer": None},
        }
        with self._state(state):
            _wh, items, _stats = pipeline.plan_stock(self.supplier)
        by_code = {i["code"]: i for i in items}
        self.assertEqual(set(by_code), {"SP.B1", "FX.123"})
        self.assertEqual(
            by_code["SP.B1"]["offer"],
            {"price_net": None, "cost_net": "5.00", "discount_percent": 30},
        )
        # papildomas sandėlis: galutinė kaina 10.00 × 0.9, nuolaida jau įskaičiuota
        self.assertEqual(
            by_code["FX.123"]["offer"],
            {"price_net": "9.00", "cost_net": "5.00", "discount_percent": None},
        )

    def test_plan_stock_locked_offer_sends_no_terms(self):
        self._offer("L1", stock=2, discount_percent=30, price_locked=True)
        with self._state({"SP.L1": {"code": "SP.L1", "qty": 2, "offer": None}}):
            _wh, items, _stats = pipeline.plan_stock(self.supplier)
        self.assertEqual(items, [])

    def test_plan_stock_old_shop_without_offer_state(self):
        self._offer("C1", stock=2, discount_percent=30)
        with self._state({"SP.C1": {"code": "SP.C1", "qty": 2}}):
            _wh, items, _stats = pipeline.plan_stock(self.supplier)
        self.assertEqual(items, [])

    def test_plan_prices_guard_lock_and_unchanged(self):
        self._offer("A1")  # 12.10 su PVM -> 10.00
        self._offer("A2", selling_price=Decimal("339.01"))  # -> 280.17
        self._offer("A3", price_locked=True)
        self._offer("A4")
        self._offer("A5")
        state = {
            "SP.A1": {"code": "SP.A1", "price_net": "9.00", "cost_net": "5.00"},
            "SP.A2": {"code": "SP.A2", "price_net": "8.26", "cost_net": None},
            "SP.A3": {"code": "SP.A3", "price_net": "1.00", "cost_net": None},
            "SP.A4": {"code": "SP.A4", "price_net": "10.00", "cost_net": "5.00"},
        }
        with self._state(state):
            items, stats = pipeline.plan_prices(self.schedule)
        self.assertEqual(items, [{"code": "SP.A1", "price_net": "10.00", "cost_net": "5.00"}])
        self.assertEqual(stats["held"], 1)
        self.assertEqual(stats["held_sample"][0]["code"], "SP.A2")
        self.assertEqual(stats["locked"], 1)
        self.assertEqual(stats["not_in_shop"], 1)

    def test_plan_prices_without_guard(self):
        self._offer("A2", selling_price=Decimal("339.01"))
        self.schedule.price_change_guard_pct = 0
        with self._state({"SP.A2": {"code": "SP.A2", "price_net": "8.26", "cost_net": None}}):
            items, stats = pipeline.plan_prices(self.schedule)
        self.assertEqual(items[0]["price_net"], "280.17")

    def test_catalog_delta_gates_and_keeps_shop_prices(self):
        self._offer("A1", product=self._product("P1"))
        self._offer("B1", product=self._product("P2", lt=False))
        self._offer("C1", product=self._product("P3", category=False))
        self._offer("D1", product=self._product("P4"))
        state = {"SP.A1": {"code": "SP.A1", "price_net": "9.00", "cost_net": "4.00"}}
        with self._state(state):
            payload, hashes, stats = pipeline.plan_catalog_delta(self.supplier)
        codes = [i["code"] for i in payload["items"]]
        self.assertEqual(sorted(codes), ["P1", "P4"])
        self.assertEqual(stats["changed"], 1)
        self.assertEqual(stats["new"], 1)
        self.assertEqual(stats["untranslated_sample"], ["P2"])
        self.assertEqual(stats["unmapped_sample"], ["P3"])
        p1 = next(i for i in payload["items"] if i["code"] == "P1")
        self.assertEqual(p1["variants"][0]["price_net"], "9.00")
        self.assertEqual(p1["variants"][0]["cost_net"], "4.00")
        p4 = next(i for i in payload["items"] if i["code"] == "P4")
        self.assertEqual(p4["variants"][0]["price_net"], "10.00")

        Product.objects.filter(sku="P1").update(exported_hash=hashes["P1"])
        with self._state(state):
            payload, _, stats = pipeline.plan_catalog_delta(self.supplier)
        self.assertEqual([i["code"] for i in payload["items"]], ["P4"])
        self.assertEqual(stats["unchanged"], 1)

    def test_catalog_payload_offers_and_secondary_skipped(self):
        product = self._product("P9")
        self._offer("D1", product=product, discount_percent=25)
        other = self._product("P10")
        self._offer("D2", product=other, shop_variant_code="FX.1")
        payload = build_catalog_payload()
        codes = [i["code"] for i in payload["items"]]
        self.assertIn("P9", codes)
        self.assertNotIn("P10", codes)
        variant = next(i for i in payload["items"] if i["code"] == "P9")["variants"][0]
        self.assertEqual(variant["offers"]["sup"]["discount_percent"], 25)
        self.assertIsNone(variant["offers"]["sup"]["price_net"])

    def test_catalog_payload_badges(self):
        product = self._product("B1")
        self._offer("BA", product=product, badges=["new-2027"])
        self._offer("BB", product=product, badges=[])
        plain = self._product("B2")
        self._offer("BC", product=plain)
        items = {i["code"]: i for i in build_catalog_payload()["items"]}
        self.assertEqual(items["B1"]["badges"], ["new-2027"])
        # tiekėjas badge'ų netvarko -> rakto nėra, shop'as jų nekeičia
        self.assertNotIn("badges", items["B2"])
        Offer.objects.filter(external_id="BA").update(badges=[])
        items = {i["code"]: i for i in build_catalog_payload()["items"]}
        self.assertEqual(items["B1"]["badges"], [])

    def test_supplier_export_keeps_other_suppliers_badges(self):
        other = Supplier.objects.create(code="oth", name="Other")
        product = self._product("B3")
        self._offer("BD", product=product, badges=[])
        self._offer("BE", product=product, supplier=other, badges=["new-2027"])
        items = {i["code"]: i for i in build_catalog_payload(self.supplier.code)["items"]}
        self.assertEqual(items["B3"]["badges"], ["new-2027"])

    def test_content_hash_badges(self):
        from apps.ingestion.dto import NormalizedItem
        from apps.ingestion.pipeline import _content_hash

        base = NormalizedItem(external_id="X", name="X")
        self.assertEqual(
            _content_hash(base), _content_hash(NormalizedItem(external_id="X", name="X", badges=None))
        )
        tagged = NormalizedItem(external_id="X", name="X", badges=["new-2027"])
        self.assertNotEqual(_content_hash(base), _content_hash(tagged))

    def test_hash_ignores_prices_and_stock(self):
        item = {"code": "X", "price_net": "1", "variants": [{"code": "v", "price_net": "1", "stock": {"a": 1}}]}
        other = {"code": "X", "price_net": "2", "variants": [{"code": "v", "price_net": "3", "stock": {"a": 9}}]}
        self.assertEqual(pipeline.item_hash(item), pipeline.item_hash(other))
        other["variants"][0]["name"] = "changed"
        self.assertNotEqual(pipeline.item_hash(item), pipeline.item_hash(other))

    def test_stage_catalog_pushes_and_stores_hash_except_failed(self):
        self._offer("A1", product=self._product("P1"))
        self._offer("D1", product=self._product("P4"))
        export = ExportRun.objects.create(
            status=ExportRun.Status.PUSHED, shop_response={"id": 5, "failed_items": 1}
        )
        with (
            self._state({}),
            patch.object(pipeline, "_translation_step", return_value=True),
            patch.object(pipeline, "run_export", return_value=export) as mock_export,
            patch.object(pipeline, "_failed_codes", return_value={"P4"}),
        ):
            run = pipeline.run_stage(self.supplier, "catalog")
        self.assertEqual(run.status, PipelineRun.Status.DONE, run.error_message)
        self.assertEqual(len(mock_export.call_args[0][1]["items"]), 2)
        self.assertTrue(Product.objects.get(sku="P1").exported_hash)
        self.assertEqual(Product.objects.get(sku="P4").exported_hash, "")
        self.schedule.refresh_from_db()
        self.assertIsNotNone(self.schedule.last_catalog_at)

    def test_stage_failure_is_recorded(self):
        with patch.object(pipeline, "fetch_variant_state", side_effect=RuntimeError("shop down")):
            run = pipeline.run_stage(self.supplier, "stock")
        self.assertEqual(run.status, PipelineRun.Status.FAILED)
        self.assertIn("shop down", run.error_message)

    def test_translation_waits_then_applies(self):
        self.schedule.translation_batch_id = "batch_1"
        self.schedule.catalog_phase = SupplierSchedule.Phase.TRANSLATING
        self.schedule.save()
        run = PipelineRun.objects.create(supplier=self.supplier, stage="catalog")
        response = MagicMock()
        response.json.return_value = {"status": "in_progress"}
        tl = "apps.ingestion.management.commands.translate_lang"
        with (
            patch(f"{tl}._openai_headers", return_value={}),
            patch.object(pipeline.requests, "get", return_value=response),
            patch(f"{tl}.Command._apply") as mock_apply,
        ):
            self.assertFalse(pipeline._translation_step(self.schedule, run, False))
            response.json.return_value = {"status": "completed"}
            self.assertTrue(pipeline._translation_step(self.schedule, run, False))
        mock_apply.assert_called_once()
        self.assertEqual(mock_apply.call_args[0][1]["batch_id"], "batch_1")
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.catalog_phase, "")
        self.assertEqual(self.schedule.translation_batch_id, "")

    def test_translation_submits_next_chunk_after_apply(self):
        self._offer("A1")
        self.schedule.translation_batch_id = "batch_1"
        self.schedule.save()
        run = PipelineRun.objects.create(supplier=self.supplier, stage="catalog")
        response = MagicMock()
        response.json.return_value = {"status": "completed"}
        tl = "apps.ingestion.management.commands.translate_lang"

        def fake_submit(_lang, _options):
            from apps.corecfg.models import ConfigEntry

            ConfigEntry.objects.update_or_create(
                key="translation_batch_id_lt", defaults={"value": "batch_2"}
            )

        with (
            patch(f"{tl}._openai_headers", return_value={}),
            patch.object(pipeline.requests, "get", return_value=response),
            patch(f"{tl}.Command._apply"),
            patch(f"{tl}.Command._submit", side_effect=fake_submit),
        ):
            self.assertFalse(pipeline._translation_step(self.schedule, run, False))
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.translation_batch_id, "batch_2")
        self.assertEqual(self.schedule.catalog_phase, "translating")

    def test_failed_batch_does_not_resubmit(self):
        self._offer("A1")
        self.schedule.translation_batch_id = "batch_1"
        self.schedule.save()
        run = PipelineRun.objects.create(supplier=self.supplier, stage="catalog")
        response = MagicMock()
        response.json.return_value = {"status": "failed"}
        tl = "apps.ingestion.management.commands.translate_lang"
        with (
            patch(f"{tl}._openai_headers", return_value={}),
            patch.object(pipeline.requests, "get", return_value=response),
            patch(f"{tl}.Command._submit") as mock_submit,
        ):
            self.assertTrue(pipeline._translation_step(self.schedule, run, False))
        mock_submit.assert_not_called()
        self.assertIn("error", run.details["translation"])

    def _job(self, external_ids, errors=0):
        from apps.ingestion.models import ImportJob, RawItem

        job = ImportJob.objects.create(supplier=self.supplier, status="success", stats={"errors": errors})
        for ext in external_ids:
            RawItem.objects.create(job=job, supplier=self.supplier, external_id=ext, payload={})
        return job

    def _absent_run(self, job, dry_run=False):
        run = PipelineRun.objects.create(supplier=self.supplier, stage="catalog")
        pipeline.mark_absent_offers(self.supplier, [job.pk], run, dry_run)
        return run

    def test_mark_absent_offers_zeroes_missing_only_when_feed_complete(self):
        for i in range(9):
            self._offer(f"S{i}")
        gone = self._offer("GONE", stock=4)
        job = self._job([f"S{i}" for i in range(9)])
        run = self._absent_run(job, dry_run=True)
        gone.refresh_from_db()
        self.assertEqual(gone.stock, 4)
        self.assertEqual(run.details["absent"]["absent"], 1)
        self._absent_run(job)
        gone.refresh_from_db()
        self.assertEqual((gone.stock, gone.is_available), (0, False))

    def test_mark_absent_skipped_for_partial_feed(self):
        self._offer("S1")
        gone = self._offer("GONE", stock=4)
        run = self._absent_run(self._job(["S1"]))
        gone.refresh_from_db()
        self.assertEqual(gone.stock, 4)
        self.assertIn("skipped", run.details["absent"])

    def test_mark_absent_skipped_when_import_had_row_errors(self):
        for i in range(9):
            self._offer(f"S{i}")
        gone = self._offer("GONE", stock=4)
        run = self._absent_run(self._job([f"S{i}" for i in range(9)], errors=1))
        gone.refresh_from_db()
        self.assertEqual(gone.stock, 4)
        self.assertIn("skipped", run.details["absent"])


class NightWindowTests(TestCase):
    def setUp(self):
        self.schedule = SupplierSchedule.objects.create(
            supplier=_supplier("night"),
            enabled=True,
            catalog_weekday=None,
            price_interval_hours=48,
            stock_interval_minutes=2880,
        )
        self.night = datetime(2026, 11, 12, 1, 0, tzinfo=VILNIUS)

    def test_prices_only_in_night_window(self):
        self.schedule.price_interval_hours = 24
        self.assertIn("prices", pipeline.due_stages(self.schedule, self.night))
        day = datetime(2026, 11, 12, 10, 0, tzinfo=VILNIUS)
        self.assertNotIn("prices", pipeline.due_stages(self.schedule, day))

    def test_every_second_night(self):
        self.schedule.last_prices_at = datetime(2026, 11, 11, 6, 50, tzinfo=VILNIUS)
        self.schedule.last_stock_at = datetime(2026, 11, 11, 6, 50, tzinfo=VILNIUS)
        self.assertEqual(pipeline.due_stages(self.schedule, self.night), [])
        later = self.night + timedelta(days=1)
        self.assertEqual(pipeline.due_stages(self.schedule, later), ["prices", "stock"])

    def test_hourly_stock_not_restricted(self):
        self.schedule.stock_interval_minutes = 60
        day = datetime(2026, 11, 12, 10, 0, tzinfo=VILNIUS)
        self.schedule.last_stock_at = day - timedelta(minutes=58)
        self.assertIn("stock", pipeline.due_stages(self.schedule, day))

    def test_catalog_only_at_night(self):
        self.schedule.catalog_phase = SupplierSchedule.Phase.TRANSLATING
        day = datetime(2026, 11, 12, 10, 0, tzinfo=VILNIUS)
        self.assertNotIn("catalog", pipeline.due_stages(self.schedule, day))
        self.assertIn("catalog", pipeline.due_stages(self.schedule, self.night))

    def test_36h_interval_rounds_up_to_two_nights(self):
        self.schedule.price_interval_hours = 36
        self.schedule.last_prices_at = datetime(2026, 11, 11, 2, 0, tzinfo=VILNIUS)
        self.assertNotIn("prices", pipeline.due_stages(self.schedule, self.night))
        self.assertIn("prices", pipeline.due_stages(self.schedule, self.night + timedelta(days=1)))

    def test_catalog_hour_outside_window_runs_at_window_start(self):
        self.schedule.catalog_weekday = self.night.weekday()
        self.schedule.catalog_hour = 23
        self.assertIn("catalog", pipeline.due_stages(self.schedule, self.night))


class UploadFeedTests(TestCase):
    def setUp(self):
        import tempfile

        from django.test import override_settings

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        media = override_settings(MEDIA_ROOT=tmp.name)
        media.enable()
        self.addCleanup(media.disable)

    def test_upload_feed_only_with_pipeline_reimport(self):
        from django.core.files.base import ContentFile

        from apps.suppliers.models import FeedRole, SourceKind, SupplierFeed

        supplier = _supplier()
        feed = SupplierFeed(
            supplier=supplier, name="Stock", role=FeedRole.CATALOG, kind=SourceKind.EXCEL_UPLOAD
        )
        feed.file.save("stock.xlsx", ContentFile(b"x"), save=False)
        feed.save()
        self.assertEqual(pipeline._feeds(supplier, [FeedRole.CATALOG]), [])
        feed.config = {"pipeline_reimport": True}
        feed.save()
        self.assertEqual(pipeline._feeds(supplier, [FeedRole.CATALOG]), [feed])
