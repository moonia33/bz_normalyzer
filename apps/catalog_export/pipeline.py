"""Automatinis tiekėjo pipeline: tiekėjas -> normalyzer -> shop.

Etapai (SupplierSchedule):
- catalog: feed'ų importas -> LT vertimas (OpenAI batch, tęsiama kitą
  paleidimą) -> į shop siunčiamos tik naujos/pasikeitusios prekės. Nauja
  prekė siunčiama tik turint kategoriją ir LT tekstą; esamų variantų kainos
  katalogo etape nekeičiamos (jas valdo prices etapas).
- prices: tik pasikeitusios kainos (lyginant su shop), dideli pokyčiai
  sulaikomi (price_change_guard_pct), Offer.price_locked – nesiunčiama.
- stock: tik pasikeitę likučiai tiekėjo sandėlyje; nutrauktos/nepublikuotos
  prekės -> 0.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

import requests
from django.db import connection
from django.utils import timezone

from apps.catalog.models import Product, ProductText
from apps.catalog_export.models import ExportRun, PipelineRun
from apps.catalog_export.services import (
    ShopClient,
    _offer_variant_code,
    _warehouse_code,
    build_catalog_payload,
    offer_prices,
    run_export,
)
from apps.corecfg.models import Language, get_config
from apps.ingestion.models import ImportJob, Offer
from apps.ingestion.pipeline import get_base_language, run_import
from apps.pricing.engine import apply_pricing
from apps.suppliers.models import (
    FeedRole,
    SourceKind,
    Supplier,
    SupplierFeed,
    SupplierSchedule,
)

logger = logging.getLogger(__name__)

LOCK_KEY = 82310001
TRANSLATION_LANG = "lt"
STATE_PATH = "/api/v1/ops/imports/variant-state"
RUNS_PATH = "/api/v1/ops/imports/runs"
STATE_CHUNK = 5000
UPLOAD_KINDS = {SourceKind.EXCEL_UPLOAD, SourceKind.CSV_UPLOAD}
DUE_TOLERANCE = timedelta(minutes=5)
CENT = Decimal("0.01")
ACTIVE_BATCH_STATUSES = {"validating", "in_progress", "finalizing", "cancelling"}
VOLATILE_ITEM_KEYS = {"price_net", "cost_net", "stock"}
VOLATILE_VARIANT_KEYS = {"price_net", "cost_net", "pack_price", "pack_purchase_price", "stock"}
LIST_LIMIT = 200
# OpenAI Batch enqueued-token limitas (~1.2M) – ~4k token/eilutė su LT promptu.
TRANSLATION_BATCH_ROWS = 250

Stage = PipelineRun.Stage


class PipelineError(Exception):
    pass


@contextmanager
def pipeline_lock():
    """Vienu metu vykdomas tik vienas pipeline (pg advisory lock)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
        locked = bool(cursor.fetchone()[0])
    try:
        yield locked
    finally:
        if locked:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])


# ------------------------------------------------------------------ due


def _catalog_due(schedule: SupplierSchedule, now) -> bool:
    if schedule.catalog_phase == SupplierSchedule.Phase.TRANSLATING:
        return True
    if schedule.catalog_weekday is None:
        return False
    local = timezone.localtime(now)
    if local.weekday() != schedule.catalog_weekday or local.hour < schedule.catalog_hour:
        return False
    return (
        schedule.last_catalog_at is None
        or timezone.localtime(schedule.last_catalog_at).date() != local.date()
    )


def _interval_due(last, interval: timedelta, now) -> bool:
    if interval <= timedelta(0):
        return False
    return last is None or now - last >= interval - DUE_TOLERANCE


def due_stages(schedule: SupplierSchedule, now=None) -> list[str]:
    now = now or timezone.now()
    if not schedule.enabled or not schedule.supplier.is_active:
        return []
    stages = []
    if _catalog_due(schedule, now):
        stages.append(Stage.CATALOG)
    if _interval_due(schedule.last_prices_at, timedelta(hours=schedule.price_interval_hours), now):
        stages.append(Stage.PRICES)
    if _interval_due(
        schedule.last_stock_at, timedelta(minutes=schedule.stock_interval_minutes), now
    ):
        stages.append(Stage.STOCK)
    return stages


def run_due(now=None, *, dry_run: bool = False) -> list[PipelineRun] | None:
    """Paleidžia visus atėjusius etapus. None – kitas pipeline dar vykdomas."""
    with pipeline_lock() as locked:
        if not locked:
            return None
        runs = []
        schedules = (
            SupplierSchedule.objects.filter(enabled=True)
            .select_related("supplier")
            .order_by("supplier__code")
        )
        for schedule in schedules:
            for stage in due_stages(schedule, now):
                runs.append(run_stage(schedule.supplier, stage, dry_run=dry_run))
        return runs


def run_stage(supplier: Supplier, stage: str, *, dry_run: bool = False) -> PipelineRun:
    schedule, _ = SupplierSchedule.objects.get_or_create(supplier=supplier)
    run = PipelineRun.objects.create(supplier=supplier, stage=stage, dry_run=dry_run)
    handler = {
        Stage.CATALOG: _stage_catalog,
        Stage.PRICES: _stage_prices,
        Stage.STOCK: _stage_stock,
    }[stage]
    try:
        run.status = handler(schedule, run, dry_run) or PipelineRun.Status.DONE
    except Exception as exc:  # noqa: BLE001 – kiti tiekėjai/etapai tęsiami
        logger.exception("Pipeline %s/%s nepavyko", supplier.code, stage)
        run.status = PipelineRun.Status.FAILED
        run.error_message = str(exc)[:2000]
    run.finished_at = timezone.now()
    run.save()
    return run


def _touch(schedule: SupplierSchedule, field: str) -> None:
    setattr(schedule, field, timezone.now())
    schedule.save(update_fields=[field, "updated_at"])


# ------------------------------------------------------------------ feeds


def _feeds(supplier: Supplier, roles, *, base_only: bool = False) -> list[SupplierFeed]:
    base = get_base_language()
    feeds = [
        feed
        for feed in SupplierFeed.objects.filter(
            supplier=supplier, is_active=True, role__in=list(roles)
        ).select_related("language")
        if feed.kind not in UPLOAD_KINDS
    ]
    if base_only:
        feeds = [f for f in feeds if f.language_id is None or (base and f.language_id == base.id)]

    def sort_key(feed: SupplierFeed):
        if base and feed.language_id == base.id:
            lang_rank = 0
        elif feed.language_id is None:
            lang_rank = 1
        else:
            lang_rank = 2
        return (0 if feed.role == FeedRole.CATALOG else 1, lang_rank, feed.pk)

    return sorted(feeds, key=sort_key)


def _import_feeds(feeds: list[SupplierFeed], run: PipelineRun) -> None:
    jobs = []
    for feed in feeds:
        job = ImportJob.objects.create(supplier=feed.supplier, feed=feed)
        try:
            run_import(job)
        except Exception:  # noqa: BLE001 – job'as pažymėtas failed
            pass
        jobs.append(
            {
                "feed": feed.pk,
                "job": job.pk,
                "status": job.status,
                "stats": {k: v for k, v in (job.stats or {}).items() if k != "error_items"},
            }
        )
    run.details["imports"] = jobs
    failed = [j["feed"] for j in jobs if j["status"] != ImportJob.Status.SUCCESS]
    if failed:
        raise PipelineError(f"feed import failed: {failed}")


# ------------------------------------------------------------------ shop


def fetch_variant_state(codes, warehouse_code: str = "") -> dict[str, dict]:
    codes = sorted(set(codes))
    if not codes:
        return {}
    client = ShopClient()
    out: dict[str, dict] = {}
    for start in range(0, len(codes), STATE_CHUNK):
        rows = client.post(
            STATE_PATH,
            {"codes": codes[start : start + STATE_CHUNK], "warehouse_code": warehouse_code},
            timeout=180,
        )
        for row in rows:
            out[row["code"]] = row
    return out


def _failed_codes(shop_response: dict) -> set[str]:
    run_id = shop_response.get("id")
    if not run_id or not shop_response.get("failed_items"):
        return set()
    client = ShopClient()
    codes: set[str] = set()
    offset = 0
    while True:
        rows = client.get(f"{RUNS_PATH}/{run_id}/items?status=failed&limit=500&offset={offset}")
        codes |= {row.get("external_code") or "" for row in rows}
        if len(rows) < 500:
            return codes
        offset += 500


def _push(kind: str, payload: dict, supplier: Supplier, run: PipelineRun) -> ExportRun:
    export = run_export(
        kind,
        payload,
        push=True,
        triggered_by="pipeline",
        options={"supplier": supplier.code, "pipeline_run": run.pk},
    )
    run.details["export_run"] = export.pk
    if export.status == ExportRun.Status.FAILED:
        raise PipelineError(export.error_message or "push failed")
    if export.error_message:
        run.details["shop_errors"] = export.error_message
    return export


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value)).quantize(CENT)


# ------------------------------------------------------------------ catalog


def item_hash(item: dict) -> str:
    data = {k: v for k, v in item.items() if k not in VOLATILE_ITEM_KEYS}
    data["variants"] = [
        {k: v for k, v in variant.items() if k not in VOLATILE_VARIANT_KEYS}
        for variant in item.get("variants") or []
    ]
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _keep_shop_prices(item: dict, state: dict[str, dict]) -> None:
    for variant in item.get("variants") or []:
        row = state.get(variant["code"])
        if row is None:
            continue
        variant["price_net"] = str(_dec(row["price_net"]))
        if row.get("cost_net") is not None:
            variant["cost_net"] = str(_dec(row["cost_net"]))


def _translation_step(schedule: SupplierSchedule, run: PipelineRun, dry_run: bool) -> bool:
    """True – galima eksportuoti; False – laukiama OpenAI batch'o."""
    from apps.ingestion.management.commands import translate_lang as tl

    lang = Language.objects.filter(code=TRANSLATION_LANG, is_active=True).first()
    if lang is None or lang.is_base:
        return True
    command = tl.Command()
    batch_id = schedule.translation_batch_id
    if batch_id:
        response = requests.get(
            f"{tl.BATCH_ENDPOINT}/{batch_id}",
            headers=tl._openai_headers(),
            timeout=30,
        )
        response.raise_for_status()
        status = response.json().get("status")
        run.details["translation"] = {"batch_id": batch_id, "status": status}
        if status in ACTIVE_BATCH_STATUSES:
            return False
        if dry_run:
            return True
        schedule.translation_batch_id = ""
        schedule.catalog_phase = SupplierSchedule.Phase.IDLE
        schedule.save(update_fields=["translation_batch_id", "catalog_phase", "updated_at"])
        if status != "completed":
            # Nesikartojam kas tick'ą: neišverstos prekės lieka nesiųstos iki kito katalogo paleidimo.
            run.details["translation"]["error"] = f"batch {status}; neišverstos prekės lieka nesiųstos"
            return True
        command._apply(lang, {"batch_id": batch_id})

    pending = command._offers_to_translate(lang, False, schedule.supplier).count()
    run.details.setdefault("translation", {})["pending"] = pending
    if not pending or dry_run:
        return True
    fd, path = tempfile.mkstemp(prefix=f"tr_{schedule.supplier.code}_", suffix=".jsonl")
    os.close(fd)
    try:
        command._prepare(
            lang,
            {
                "seo_only": False,
                "model": None,
                "force": False,
                "limit": TRANSLATION_BATCH_ROWS,
                "out": path,
                "supplier": schedule.supplier.code,
            },
        )
        if os.path.getsize(path) == 0:
            return True
        command._submit(lang, {"out": path, "model": None})
    finally:
        os.unlink(path)
    batch_id = get_config(tl._config_key(tl.CONFIG_BATCH_PREFIX, lang.code), "")
    schedule.translation_batch_id = batch_id
    schedule.catalog_phase = SupplierSchedule.Phase.TRANSLATING
    schedule.save(update_fields=["translation_batch_id", "catalog_phase", "updated_at"])
    run.details["translation"]["batch_id"] = batch_id
    return False


def plan_catalog_delta(supplier: Supplier) -> tuple[dict, dict[str, str], dict]:
    """(payload su siunčiamomis prekėmis, {code: hash}, statistika)."""
    payload = build_catalog_payload(supplier_code=supplier.code)
    items = payload["items"]
    products = {p.sku: p for p in Product.objects.filter(sku__in=[i["code"] for i in items])}
    with_lt = set(
        ProductText.objects.filter(
            product__in=list(products.values()), language__code=TRANSLATION_LANG
        )
        .exclude(name="")
        .values_list("product_id", flat=True)
    )
    state = fetch_variant_state(v["code"] for i in items for v in i.get("variants") or [])
    send, hashes = [], {}
    counts: Counter = Counter()
    unmapped, untranslated = [], []
    for item in items:
        product = products.get(item["code"])
        digest = item_hash(item)
        if product is not None and product.exported_hash == digest:
            counts["unchanged"] += 1
            continue
        if product is None or product.id not in with_lt:
            untranslated.append(item["code"])
            continue
        in_shop = any(v["code"] in state for v in item.get("variants") or [])
        if not in_shop and not item.get("category") and not item.get("is_part"):
            unmapped.append(item["code"])
            continue
        counts["changed" if in_shop else "new"] += 1
        _keep_shop_prices(item, state)
        send.append(item)
        hashes[item["code"]] = digest
    payload["items"] = send
    stats = {
        "items": len(items),
        **counts,
        "unmapped_category": len(unmapped),
        "unmapped_sample": unmapped[:LIST_LIMIT],
        "untranslated": len(untranslated),
        "untranslated_sample": untranslated[:LIST_LIMIT],
    }
    return payload, hashes, stats


def _stage_catalog(schedule: SupplierSchedule, run: PipelineRun, dry_run: bool):
    supplier = schedule.supplier
    resuming = schedule.catalog_phase == SupplierSchedule.Phase.TRANSLATING
    if not resuming and not dry_run:
        _touch(schedule, "last_catalog_at")
        _import_feeds(_feeds(supplier, [FeedRole.CATALOG, FeedRole.DISCONTINUED]), run)
    if not _translation_step(schedule, run, dry_run):
        return PipelineRun.Status.WAITING
    payload, hashes, stats = plan_catalog_delta(supplier)
    run.details["catalog"] = stats
    if dry_run or not payload["items"]:
        return None
    export = _push(ExportRun.Kind.CATALOG, payload, supplier, run)
    failed = _failed_codes(export.shop_response or {})
    products = list(Product.objects.filter(sku__in=[c for c in hashes if c not in failed]))
    for product in products:
        product.exported_hash = hashes[product.sku]
    Product.objects.bulk_update(products, ["exported_hash"], batch_size=500)
    run.details["catalog"]["pushed"] = len(products)
    run.details["catalog"]["failed"] = sorted(failed)[:LIST_LIMIT]
    return None


def baseline_catalog(supplier: Supplier) -> dict:
    """Pažymi shop'e jau esančias prekes kaip sinchronizuotas (nieko nesiunčia)."""
    payload = build_catalog_payload(supplier_code=supplier.code)
    state = fetch_variant_state(
        v["code"] for i in payload["items"] for v in i.get("variants") or []
    )
    marked = 0
    for item in payload["items"]:
        if any(v["code"] in state for v in item.get("variants") or []):
            marked += Product.objects.filter(sku=item["code"]).update(
                exported_hash=item_hash(item)
            )
    return {"items": len(payload["items"]), "marked": marked}


# ------------------------------------------------------------------ prices


def plan_prices(schedule: SupplierSchedule) -> tuple[list[dict], dict]:
    supplier = schedule.supplier
    desired: dict[str, tuple] = {}
    locked = 0
    offers = Offer.objects.filter(
        supplier=supplier, is_published=True, is_discontinued=False
    ).select_related("supplier")
    for offer in offers.iterator():
        price, cost, _pack, _pack_cost = offer_prices(offer)
        if price is None:
            continue
        if offer.price_locked:
            locked += 1
            continue
        desired[_offer_variant_code(offer)] = (_dec(price), _dec(cost))
    state = fetch_variant_state(desired)
    guard = Decimal(schedule.price_change_guard_pct or 0)
    items, held = [], []
    not_in_shop = 0
    for code, (price, cost) in sorted(desired.items()):
        row = state.get(code)
        if row is None:
            not_in_shop += 1
            continue
        shop_price, shop_cost = _dec(row["price_net"]), _dec(row.get("cost_net"))
        if price == shop_price and (cost is None or cost == shop_cost):
            continue
        if (
            price != shop_price
            and guard
            and shop_price
            and abs(price - shop_price) / shop_price * 100 > guard
        ):
            held.append({"code": code, "shop": str(shop_price), "supplier": str(price)})
            continue
        items.append(
            {
                "code": code,
                "price_net": str(price),
                "cost_net": str(cost) if cost is not None else None,
            }
        )
    stats = {
        "offers": len(desired),
        "changed": len(items),
        "held": len(held),
        "held_sample": held[:LIST_LIMIT],
        "locked": locked,
        "not_in_shop": not_in_shop,
    }
    return items, stats


def _stage_prices(schedule: SupplierSchedule, run: PipelineRun, dry_run: bool):
    supplier = schedule.supplier
    if not dry_run:
        _touch(schedule, "last_prices_at")
        _import_feeds(_feeds(supplier, [FeedRole.CATALOG], base_only=True), run)
        apply_pricing(Offer.objects.filter(supplier=supplier).select_related("supplier"))
    items, stats = plan_prices(schedule)
    run.details["prices"] = stats
    if items and not dry_run:
        _push(
            ExportRun.Kind.PRICE,
            {"format_version": 1, "source": "normalyzer", "items": items},
            supplier,
            run,
        )
    return None


# ------------------------------------------------------------------ stock


def plan_stock(supplier: Supplier) -> tuple[str, list[dict], dict]:
    warehouse = _warehouse_code(supplier)
    desired: dict[str, int] = {}
    for offer in Offer.objects.filter(supplier=supplier).select_related("supplier").iterator():
        live = offer.is_published and offer.is_available and not offer.is_discontinued
        qty = max(offer.stock or 0, 0) if live else 0
        code = _offer_variant_code(offer)
        desired[code] = max(desired.get(code, 0), qty)
    state = fetch_variant_state(desired, warehouse)
    items = [
        {"code": code, "qty": qty}
        for code, qty in sorted(desired.items())
        if code in state and state[code].get("qty") != qty
    ]
    stats = {
        "offers": len(desired),
        "in_shop": sum(1 for code in desired if code in state),
        "changed": len(items),
        "warehouse": warehouse,
    }
    return warehouse, items, stats


def _stage_stock(schedule: SupplierSchedule, run: PipelineRun, dry_run: bool):
    supplier = schedule.supplier
    if not dry_run:
        _touch(schedule, "last_stock_at")
        feeds = _feeds(supplier, [FeedRole.STOCK]) or _feeds(
            supplier, [FeedRole.CATALOG], base_only=True
        )
        _import_feeds(feeds, run)
    warehouse, items, stats = plan_stock(supplier)
    run.details["stock"] = stats
    if items and not dry_run:
        _push(
            ExportRun.Kind.STOCK,
            {
                "format_version": 1,
                "source": "normalyzer",
                "warehouse_code": warehouse,
                "items": items,
            },
            supplier,
            run,
        )
    return None
