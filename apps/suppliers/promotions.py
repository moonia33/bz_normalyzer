"""Tiekėjų akcijos (išpardavimai), kurių nėra feed failuose – nuskaitomos iš B2B.

Fox / CC Moore: order.foxtackle.biz „Discount & Special Offers“ (CLEAR20…CLEAR80).
Akcijos % – papildoma nuolaida nuo pirkimo kainos; tą patį % taikome nuo pardavimo
kainos (`Offer.discount_percent`), todėl marža % nesikeičia. FC/CC (bendros sąskaitos
nuolaidos) netaikomos.

Sinchronizuojama likučių etape (`_stage_stock`), po likučių feed'o importo. Pranešimas
el. paštu tik apie naujas (su likučiu) ir pasibaigusias išpardavimo prekes.
Nuskaitymas nepavyko – akcijos ir nuolaidos nekeičiamos (klaida ≠ akcija baigėsi).
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from html import escape, unescape

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.ingestion.models import Offer
from apps.suppliers.models import Supplier, SupplierPromotion

logger = logging.getLogger(__name__)

FOX_BASE_URL = "https://order.foxtackle.biz"
FOX_APPLIED_PREFIXES = ("CLEAR",)
FOX_TIMEOUT = 60
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)

_REF_RE = re.compile(
    r'class="getDiscLink"\s+id="([^"]+)".*?<b>Offer</b>\s*=\s*(\d+)\s*%', re.S
)
_TOKEN_RE = re.compile(r'id="basket_token"[^>]*>\s*([^<\s]+)\s*<')
_ITEM_RE = re.compile(r'class="addDiscoProd"[^>]*\bdata-link="([^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")
_REF_LIST_MARK = "Products available for discount code"


class PromotionError(Exception):
    pass


# ------------------------------------------------------------------ Fox B2B


def parse_fox_offer_refs(html: str) -> dict[str, int]:
    """{akcijos kodas: %} iš užsakymo puslapio."""
    return {ref: int(pct) for ref, pct in _REF_RE.findall(html)}


def parse_fox_ref_items(html: str) -> list[tuple[str, str]]:
    """[(prekės kodas, pavadinimas)] iš akcijos prekių sąrašo (ajax-discounts.php).

    Kodai imami iš kiekvieno `addDiscoProd` (pavadinimo HTML jų neištrina);
    pavadinimas – tekstas po kodo iki eilutės pabaigos.
    """
    matches = list(_ITEM_RE.finditer(html))
    out = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        segment = re.split(r"<br\s*/?>", html[match.end():end], maxsplit=1)[0]
        text = unescape(_TAG_RE.sub("", segment.split("</a>", 1)[-1])).strip()
        code = match.group(1).strip()
        out.append((code, text.lstrip("-").strip()))
    return out


def fetch_fox_clearance() -> dict[str, dict]:
    """{kodas: {reference, discount_percent, name}} – tik FOX_APPLIED_PREFIXES akcijos."""
    import requests

    username = getattr(settings, "FOX_B2B_USERNAME", "")
    password = getattr(settings, "FOX_B2B_PASSWORD", "")
    if not username or not password:
        raise PromotionError("Fox B2B: nenurodyti FOX_B2B_USERNAME / FOX_B2B_PASSWORD")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    index = f"{FOX_BASE_URL}/index.php"
    session.get(index, timeout=FOX_TIMEOUT).raise_for_status()
    session.post(
        index,
        data={"formloginuser": username, "formloginpass": password, "submitbutton": "Login"},
        timeout=FOX_TIMEOUT,
    ).raise_for_status()
    page = session.get(index, params={"action": "order"}, timeout=FOX_TIMEOUT)
    page.raise_for_status()
    html = page.text
    if "formloginpass" in html:
        raise PromotionError("Fox B2B: prisijungti nepavyko")
    refs = parse_fox_offer_refs(html)
    token = _TOKEN_RE.search(html)
    if not refs or not token:
        raise PromotionError("Fox B2B: akcijų sąrašas nerastas (pasikeitė puslapis?)")
    out: dict[str, dict] = {}
    for ref, pct in sorted(refs.items()):
        if not ref.upper().startswith(FOX_APPLIED_PREFIXES):
            continue
        response = session.post(
            f"{FOX_BASE_URL}/javascript/ajax-discounts.php",
            data={"action": "getdiscount", "idToken": token.group(1), "product": ref},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": page.url},
            timeout=FOX_TIMEOUT,
        )
        response.raise_for_status()
        if _REF_LIST_MARK not in response.text:
            raise PromotionError(f"Fox B2B: nepavyko gauti {ref} prekių sąrašo")
        items = parse_fox_ref_items(response.text)
        if len(items) != response.text.count("addDiscoProd"):
            raise PromotionError(f"Fox B2B: {ref} sąrašas nuskaitytas nepilnai")
        for code, name in items:
            key = code.upper()
            if key not in out or pct > out[key]["discount_percent"]:
                out[key] = {"reference": ref, "discount_percent": pct, "name": name}
    return out


SOURCES = {"fox": fetch_fox_clearance}


# ------------------------------------------------------------------ sync


def _code(offer: Offer) -> str:
    return ((offer.sku or "").strip() or offer.external_id).upper()


def _in_stock(offer: Offer) -> bool:
    return bool(offer.is_available and (offer.stock or 0) > 0 and not offer.is_discontinued)


def apply_discounts(supplier: Supplier) -> int:
    """Offer.discount_percent = aktyvios akcijos % (be akcijos – None).

    Tik tiekėjams su akcijų šaltiniu (jų feed'ai nuolaidos neturi). Kviečiama po
    kiekvieno importo, nes pilnas katalogo importas nuolaidą nunulina.
    """
    if supplier.code not in SOURCES:
        return 0
    active = dict(
        SupplierPromotion.objects.filter(supplier=supplier, is_active=True).values_list(
            "code", "discount_percent"
        )
    )
    changed = []
    for offer in Offer.objects.filter(supplier=supplier).only(
        "id", "sku", "external_id", "discount_percent"
    ):
        wanted = active.get(_code(offer))
        if offer.discount_percent != wanted:
            offer.discount_percent = wanted
            changed.append(offer)
    Offer.objects.bulk_update(changed, ["discount_percent"], batch_size=500)
    return len(changed)


def update_promotions(supplier: Supplier, listed: dict[str, dict]) -> dict[str, list[dict]]:
    """Atnaujina SupplierPromotion pagal nuskaitytą sąrašą; grąžina įvykius pranešimui."""
    now = timezone.now()
    offers: dict[str, Offer] = {}
    for offer in Offer.objects.filter(supplier=supplier):
        offers.setdefault(_code(offer), offer)
    existing = {p.code: p for p in SupplierPromotion.objects.filter(supplier=supplier)}
    events: dict[str, list[dict]] = {"new": [], "ended": []}

    def event(kind: str, promo: SupplierPromotion, reason: str) -> None:
        offer = offers.get(promo.code)
        events[kind].append(
            {
                "code": f"{supplier.sku_prefix or ''}{promo.code}",
                "name": (offer.name if offer else "") or promo.name,
                "reference": promo.reference,
                "discount_percent": promo.discount_percent,
                "purchase_price": offer.purchase_price if offer else None,
                "rrp_price": offer.rrp_price if offer else None,
                "reason": reason,
            }
        )

    with transaction.atomic():
        for code, data in listed.items():
            offer = offers.get(code)
            if offer is None:
                continue  # ne mūsų kataloge (nėra tiekėjo feed'e) – neįtraukiama
            in_stock = _in_stock(offer)
            promo = existing.get(code)
            if promo is None:
                promo = SupplierPromotion(supplier=supplier, code=code, is_active=False)
            was_active, was_in_stock = promo.pk and promo.is_active, promo.in_stock
            promo.name = data.get("name", "")[:500]
            promo.reference = data["reference"]
            promo.discount_percent = data["discount_percent"]
            promo.in_stock = in_stock
            promo.is_active = True
            promo.ended_at = None
            promo.last_seen_at = now
            promo.save()
            if in_stock and not (was_active and was_in_stock):
                event("new", promo, "nauja" if not was_active else "vėl yra likutis")
            elif was_active and was_in_stock and not in_stock:
                event("ended", promo, "baigėsi likutis")
        for code, promo in existing.items():
            if promo.is_active and code not in listed:
                if promo.in_stock:
                    event("ended", promo, "išimta iš išpardavimo")
                promo.is_active = False
                promo.ended_at = now
                promo.save(update_fields=["is_active", "ended_at", "updated_at"])
    return events


def sync_promotions(supplier: Supplier, *, notify: bool = True) -> dict | None:
    source = SOURCES.get(supplier.code)
    if source is None:
        return None
    try:
        listed = source()
    except Exception as exc:  # noqa: BLE001 – likučių etapas tęsiamas
        logger.exception("Akcijų nuskaitymas nepavyko (%s)", supplier.code)
        if notify:
            _safe(_alert_failure, supplier, exc)
        return {"error": str(exc)[:300], "discounts_changed": apply_discounts(supplier)}
    events = update_promotions(supplier, listed)
    stats = {
        "listed": len(listed),
        "active": SupplierPromotion.objects.filter(supplier=supplier, is_active=True).count(),
        "new": len(events["new"]),
        "ended": len(events["ended"]),
        "discounts_changed": apply_discounts(supplier),
    }
    if notify and (events["new"] or events["ended"]):
        _safe(_alert_events, supplier, events)
    return stats


@contextmanager
def preview_promotions(supplier: Supplier):
    """Dry-run: nuskaito ir pritaiko akcijas transakcijoje, kuri atšaukiama.

    Bloke (pvz. `plan_stock`) matomos būsimos nuolaidos; DB ir laiškai nekeičiami.
    """
    with transaction.atomic():
        stats = sync_promotions(supplier, notify=False)
        try:
            yield stats
        finally:
            transaction.set_rollback(True)


# ------------------------------------------------------------------ pranešimai


def _safe(func, *args) -> None:
    try:
        func(*args)
    except Exception:  # noqa: BLE001 – pranešimai negali sustabdyti pipeline
        logger.exception("Akcijų pranešimas nepavyko")


def _recipients() -> list[str]:
    return list(
        getattr(settings, "PROMOTION_ALERT_EMAILS", None)
        or getattr(settings, "PIPELINE_ALERT_EMAILS", [])
        or []
    )


def _money(value) -> str:
    return f"{value:.2f} €" if value is not None else "-"


def _alert_events(supplier: Supplier, events: dict[str, list[dict]]) -> None:
    from apps.catalog_export.notifications import _send_once

    parts = []
    if events["new"]:
        parts.append(f"{len(events['new'])} naujos")
    if events["ended"]:
        parts.append(f"{len(events['ended'])} pasibaigė")
    subject = f"{supplier.name} išpardavimas: {', '.join(parts)}"
    text_lines, html_parts = [], []
    for kind, title in (("new", "Naujos išpardavimo prekės (su likučiu)"), ("ended", "Pasibaigė")):
        rows = sorted(events[kind], key=lambda r: (-r["discount_percent"], r["code"]))
        if not rows:
            continue
        text_lines.append(f"{title} ({len(rows)}):")
        cells = []
        for r in rows:
            text_lines.append(
                f"  {r['code']}  {r['name']}  -{r['discount_percent']}% ({r['reference']})  "
                f"pirkimo {_money(r['purchase_price'])}, RRP {_money(r['rrp_price'])}  [{r['reason']}]"
            )
            cells.append(
                "<tr>"
                + "".join(
                    f"<td style='padding:2px 8px'>{escape(str(v))}</td>"
                    for v in (
                        r["code"], r["name"], f"-{r['discount_percent']} %", r["reference"],
                        _money(r["purchase_price"]), _money(r["rrp_price"]), r["reason"],
                    )
                )
                + "</tr>"
            )
        text_lines.append("")
        head = "".join(
            f"<th style='text-align:left;padding:2px 8px'>{h}</th>"
            for h in ("Kodas", "Prekė", "Nuolaida", "Akcija", "Pirkimo kaina", "RRP", "Būsena")
        )
        html_parts.append(
            f"<h3>{title} ({len(rows)})</h3><table style='border-collapse:collapse'>"
            f"<tr>{head}</tr>{''.join(cells)}</table>"
        )
    stamp = timezone.now()
    _send_once(
        f"promo:{supplier.code}:{stamp:%Y%m%d%H%M%S%f}",
        subject,
        "\n".join(text_lines),
        "".join(html_parts),
        recipients=_recipients(),
    )


def _alert_failure(supplier: Supplier, exc: Exception) -> None:
    from apps.catalog_export.notifications import _send_once

    _send_once(
        f"promo-failed:{supplier.code}:{timezone.localdate():%Y%m%d}",
        f"{supplier.name} išpardavimų nuskaityti nepavyko",
        f"Klaida: {exc}\n\nAkcijos ir nuolaidos nepakeistos. Kitas bandymas – kitame likučių etape.\n",
    )
