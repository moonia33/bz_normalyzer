"""Pipeline pranešimai el. paštu: klaidos iškart + kasdienė suvestinė.

- alert_run: paleidimas nepavyko arba sulaikytos kainos (> apsaugos ribos).
- tick (kas 15 min. po run_due): užstrigę / nutrūkę paleidimai, nepasileidę
  etapai (pagal SupplierSchedule) ir kasdienė suvestinė PIPELINE_SUMMARY_HOUR val.
Pakartotiniai pranešimai neišsiunčiami – raktas įrašomas į PipelineAlert.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from html import escape

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog_export.models import PipelineAlert, PipelineRun
from apps.suppliers.models import SupplierSchedule

logger = logging.getLogger(__name__)

Status = PipelineRun.Status
Stage = PipelineRun.Stage
STUCK_ALERT_AFTER = timedelta(minutes=90)
STALE_AFTER = timedelta(hours=6)
MISSED_GRACE = timedelta(hours=1)
STAGE_LT = {Stage.CATALOG: "katalogas", Stage.PRICES: "kainos", Stage.STOCK: "likučiai"}
STATUS_LT = {
    Status.DONE: "pavyko",
    Status.FAILED: "klaida",
    Status.WAITING: "laukia vertimo",
    Status.RUNNING: "vykdoma",
}


def _recipients() -> list[str]:
    return list(getattr(settings, "PIPELINE_ALERT_EMAILS", []) or [])


def _send_once(key: str, subject: str, text: str, html: str | None = None) -> bool:
    recipients = _recipients()
    if not recipients:
        return False
    try:
        with transaction.atomic():
            PipelineAlert.objects.create(key=key[:200], subject=subject[:300])
    except IntegrityError:
        return False
    try:
        send_mail(
            f"[normalyzer] {subject}",
            text,
            settings.DEFAULT_FROM_EMAIL,
            recipients,
            html_message=html,
        )
    except Exception:
        PipelineAlert.objects.filter(key=key[:200]).delete()
        raise
    return True


def _label(run: PipelineRun) -> str:
    return f"{run.supplier.code} {STAGE_LT.get(run.stage, run.stage)} #{run.pk}"


def _admin_url(run: PipelineRun) -> str:
    base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").rstrip("/")
    path = f"/admin/catalog_export/pipelinerun/{run.pk}/change/"
    return f"{base}{path}" if base else path


# --- klaidos iškart ---------------------------------------------------------


def alert_run(run: PipelineRun) -> None:
    if run.dry_run:
        return
    if run.status == Status.FAILED:
        _send_once(
            f"failed:{run.pk}",
            f"KLAIDA: {_label(run)}",
            f"{_label(run)} nepavyko {timezone.localtime(run.finished_at or timezone.now()):%Y-%m-%d %H:%M}.\n\n"
            f"Klaida: {run.error_message or '-'}\n\n{_admin_url(run)}\n",
        )
        return
    prices = (run.details or {}).get("prices") or {}
    if prices.get("held"):
        sample = ", ".join(str(s) for s in (prices.get("held_sample") or [])[:20])
        _send_once(
            f"held:{run.pk}",
            f"Sulaikytos kainos: {run.supplier.code} ({prices['held']})",
            f"{_label(run)}: {prices['held']} kainų pokyčiai viršija apsaugos ribą ir nesiųsti į BE.\n"
            f"Pavyzdžiai: {sample or '-'}\n\nPeržiūrėkite normalyzer admin'e.\n{_admin_url(run)}\n",
        )


def safe_alert_run(run: PipelineRun) -> None:
    try:
        alert_run(run)
    except Exception:  # noqa: BLE001 – pranešimai negali sustabdyti pipeline
        logger.exception("Pipeline pranešimas nepavyko (run #%s)", run.pk)


# --- periodiniai patikrinimai ----------------------------------------------


def check_stuck(now, *, mark_stale: bool = True) -> None:
    for run in PipelineRun.objects.filter(status=Status.RUNNING, dry_run=False).select_related(
        "supplier"
    ):
        age = now - run.created_at
        if mark_stale and age >= STALE_AFTER:
            run.status = Status.FAILED
            run.error_message = (
                f"Nutrūko: paleidimas nebaigtas per {int(age.total_seconds() // 3600)} val. "
                "(procesas nutrauktas, pvz. deploy ar konteinerio perkrovimas)."
            )
            run.finished_at = now
            run.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
            alert_run(run)
        elif age >= STUCK_ALERT_AFTER:
            _send_once(
                f"stuck:{run.pk}",
                f"Užstrigo: {_label(run)}",
                f"{_label(run)} vykdomas jau {int(age.total_seconds() // 60)} min. "
                f"(pradėtas {timezone.localtime(run.created_at):%H:%M}).\n\n{_admin_url(run)}\n",
            )


def _overdue(last, interval: timedelta, now) -> bool:
    return interval > timedelta(0) and last is not None and now - last > interval + MISSED_GRACE


def _missed(schedule: SupplierSchedule, stage: str, last, interval: timedelta, now) -> bool:
    """Naktiniai etapai tikrinami po nakties lango (+1 val.), kiti – pagal intervalą."""
    from apps.catalog_export import pipeline

    if not pipeline.stage_is_nightly(stage, schedule):
        return _overdue(last, interval, now)
    if interval <= timedelta(0) or last is None:
        return False
    if timezone.localtime(now).hour < SupplierSchedule.NIGHT_END_HOUR + 1:
        return False
    if interval >= timedelta(days=1):
        return pipeline.nights_between(last, now) >= pipeline.required_nights(interval)
    return pipeline.nights_between(last, now) >= 1


def check_missed(now) -> None:
    day = timezone.localtime(now).date().isoformat()
    for schedule in SupplierSchedule.objects.filter(enabled=True, supplier__is_active=True).select_related(
        "supplier"
    ):
        code = schedule.supplier.code
        checks = [
            (Stage.PRICES, schedule.last_prices_at, timedelta(hours=schedule.price_interval_hours)),
            (Stage.STOCK, schedule.last_stock_at, timedelta(minutes=schedule.stock_interval_minutes)),
        ]
        for stage, last, interval in checks:
            if _missed(schedule, stage, last, interval, now):
                _send_once(
                    f"missed:{code}:{stage}:{day}",
                    f"Nepasileido: {code} {STAGE_LT[stage]}",
                    f"{code} {STAGE_LT[stage]} turėjo vykti kas {interval}, paskutinis paleidimas "
                    f"{timezone.localtime(last):%Y-%m-%d %H:%M}.\n"
                    "Patikrinkite Coolify scheduled task 'supplier-pipeline' ir PipelineRun žurnalą.\n",
                )


# --- kasdienė suvestinė -----------------------------------------------------


def _expected(schedule: SupplierSchedule, since, until) -> dict[str, int]:
    from apps.catalog_export import pipeline

    hours = (until - since).total_seconds() / 3600
    expected = {Stage.CATALOG: 0, Stage.PRICES: 0, Stage.STOCK: 0}
    if schedule.catalog_weekday is not None:
        day = timezone.localtime(since).date()
        while day <= timezone.localtime(until).date():
            if day.weekday() == schedule.catalog_weekday:
                start = timezone.make_aware(
                    timezone.datetime(day.year, day.month, day.day, pipeline.catalog_start_hour(schedule))
                )
                if since <= start < until:
                    expected[Stage.CATALOG] += 1
            day += timedelta(days=1)
    if schedule.price_interval_hours:
        price_hours = hours
        if schedule.price_interval_hours < 24:
            window = SupplierSchedule.NIGHT_END_HOUR - SupplierSchedule.NIGHT_START_HOUR
            price_hours = hours * window / 24
        expected[Stage.PRICES] = int(price_hours // schedule.price_interval_hours)
    if schedule.stock_interval_minutes:
        expected[Stage.STOCK] = int(hours * 60 // schedule.stock_interval_minutes)
    return expected


def _metrics(runs: list[PipelineRun]) -> dict[str, int]:
    m: dict[str, int] = defaultdict(int)
    for run in runs:
        d = run.details or {}
        cat = d.get("catalog") or {}
        m["eksportuota"] += int(cat.get("pushed") or 0)
        m["nesumapinta"] = max(m["nesumapinta"], int(cat.get("unmapped_category") or 0))
        m["be LT teksto"] = max(m["be LT teksto"], int(cat.get("untranslated") or 0))
        tr = d.get("translation") or {}
        m["laukė vertimo"] = max(m["laukė vertimo"], int(tr.get("pending") or 0))
        m["kainų pokyčiai"] += int((d.get("prices") or {}).get("changed") or 0)
        m["sulaikytos kainos"] += int((d.get("prices") or {}).get("held") or 0)
        m["likučių pokyčiai"] += int((d.get("stock") or {}).get("changed") or 0)
        absent = d.get("absent") or {}
        if not absent.get("skipped"):
            m["dingo iš feed (likutis 0)"] = max(m["dingo iš feed (likutis 0)"], int(absent.get("absent") or 0))
        for imp in d.get("imports") or []:
            stats = imp.get("stats") or {}
            m["importuota eilučių"] += int(stats.get("upserted") or 0)
            m["importo klaidos"] += int(stats.get("errors") or 0)
    return {k: v for k, v in m.items() if v}


def build_summary(now) -> tuple[str, str, str]:
    until = now
    since = now - timedelta(days=1)
    runs = list(
        PipelineRun.objects.filter(created_at__gte=since, created_at__lt=until, dry_run=False)
        .select_related("supplier")
        .order_by("created_at")
    )
    by_supplier: dict[str, list[PipelineRun]] = defaultdict(list)
    for run in runs:
        by_supplier[run.supplier.code].append(run)
    schedules = list(
        SupplierSchedule.objects.filter(enabled=True).select_related("supplier").order_by("supplier__code")
    )
    problems = 0
    rows, text_lines, failures = [], [], []
    for schedule in schedules:
        code = schedule.supplier.code
        sruns = by_supplier.get(code, [])
        expected = _expected(schedule, since, until)
        for stage in (Stage.CATALOG, Stage.PRICES, Stage.STOCK):
            stage_runs = [r for r in sruns if r.stage == stage]
            counts = defaultdict(int)
            for r in stage_runs:
                counts[r.status] += 1
            exp = expected[stage]
            if not exp and not stage_runs:
                continue
            bad = counts[Status.FAILED] + counts[Status.RUNNING]
            missing = max(exp - len(stage_runs), 0) if stage != Stage.CATALOG else max(exp - (1 if stage_runs else 0), 0)
            ok = not bad and not missing
            problems += 0 if ok else 1
            state = "OK" if ok else "PROBLEMA"
            metrics = _metrics(stage_runs)
            mtxt = ", ".join(f"{k}: {v}" for k, v in metrics.items()) or "-"
            actual = ", ".join(f"{STATUS_LT.get(s, s)} {n}" for s, n in counts.items()) or "nebuvo"
            if missing:
                actual += f", nepasileido {missing}"
            rows.append((code, STAGE_LT[stage], str(exp), actual, mtxt, state))
            text_lines.append(f"{state:8} {code:15} {STAGE_LT[stage]:9} turėjo {exp:>3} | įvyko: {actual} | {mtxt}")
            failures += [r for r in stage_runs if r.status == Status.FAILED]
    day = timezone.localtime(now)
    subject = (
        f"Suvestinė {day:%Y-%m-%d}: " + (f"{problems} problemos" if problems else "viskas gerai")
    )
    text = f"Pipeline suvestinė už {timezone.localtime(since):%m-%d %H:%M} – {day:%m-%d %H:%M}\n\n"
    text += "\n".join(text_lines) or "Paleidimų nebuvo."
    if failures:
        text += "\n\nKlaidos:\n" + "\n".join(f"- {_label(r)}: {r.error_message[:300]}" for r in failures)
    th = "".join(f"<th align=left>{h}</th>" for h in ("Tiekėjas", "Etapas", "Turėjo", "Įvyko", "Rezultatai", ""))
    tr = "".join(
        "<tr>" + "".join(
            f"<td style='padding:2px 8px;{'color:#b00;font-weight:bold' if i == 5 and c != 'OK' else ''}'>{escape(c)}</td>"
            for i, c in enumerate(row)
        ) + "</tr>"
        for row in rows
    )
    html = f"<p>{escape(text.splitlines()[0])}</p><table border=1 cellspacing=0>{th}{tr}</table>"
    if failures:
        html += "<h4>Klaidos</h4><ul>" + "".join(
            f"<li>{escape(_label(r))}: {escape(r.error_message[:300])}</li>" for r in failures
        ) + "</ul>"
    return subject, text, html


def send_daily_summary(now, *, force: bool = False) -> bool:
    local = timezone.localtime(now)
    if not force and local.hour < getattr(settings, "PIPELINE_SUMMARY_HOUR", 7):
        return False
    subject, text, html = build_summary(now)
    key = f"summary:{local.date().isoformat()}" + (f":{now.timestamp()}" if force else "")
    return _send_once(key, subject, text, html)


def retry_recent_alerts(now) -> None:
    """Pakartoja per SMTP klaidą neišsiųstus paleidimų pranešimus (raktas tada ištrinamas)."""
    for run in PipelineRun.objects.filter(
        dry_run=False, finished_at__gte=now - timedelta(days=1)
    ).select_related("supplier"):
        alert_run(run)


def tick(now=None, *, mark_stale: bool = True) -> None:
    """mark_stale=False – kai pipeline užraktas užimtas (etapas dar tikrai vykdomas)."""
    now = now or timezone.now()
    check_stuck(now, mark_stale=mark_stale)
    if not mark_stale:
        return
    retry_recent_alerts(now)
    check_missed(now)
    send_daily_summary(now)


def safe_tick(now=None, *, mark_stale: bool = True) -> None:
    try:
        tick(now, mark_stale=mark_stale)
    except Exception:  # noqa: BLE001
        logger.exception("Pipeline pranešimų patikra nepavyko")
