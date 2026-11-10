"""Tikrina tiekėjo Offer nuotraukų URL (GET Range 0-0) ir įrašo neveikiančius.

Albatros Stock FOTO stulpelyje dalis images.salsify.com nuorodų grąžina 404 – shop'e jos
rodomos kaip sugadintos nuotraukos. Adapteris neveikiančius URL praleidžia (prekė be
veikiančios nuotraukos nepublikuojama). Paleisti prieš importą:
  manage.py check_image_urls --supplier albatros [--host images.salsify.com] [--recheck-days 30]
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import quote, urlsplit, urlunsplit

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from apps.ingestion.models import Offer
from apps.suppliers.models import ImageUrlCheck

USER_AGENT = "Mozilla/5.0 (compatible; bz-normalyzer/1.0; +https://baltijoszvynas.lt)"
# 404/410 – tikrai nėra; 5xx/timeout – laikina klaida, nuotrauka neatmetama.
DEAD_STATUSES = {400, 403, 404, 410}


def _public_host(url: str) -> bool:
    """Tik http(s) ir viešas IP – tiekėjo URL negali pasiekti vidinio tinklo (SSRF)."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _public_host(newurl):
            raise urllib.error.URLError(f"redirect to non-public host: {newurl[:200]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def probe(url: str, timeout: int = 20) -> tuple[bool, int | None, str]:
    try:
        parts = urlsplit(url)
        safe = urlunsplit(
            (parts.scheme, parts.netloc, quote(parts.path, safe="/%:@!$&'()*+,;=-._~"), parts.query, "")
        )
        if not _public_host(safe):
            return False, None, "non-public or invalid host"
        req = urllib.request.Request(safe, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"})
        with _OPENER.open(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if ctype and not ctype.startswith(("image/", "application/octet-stream", "binary/")):
                return False, resp.status, f"content-type {ctype}"[:300]
            return True, resp.status, ""
    except urllib.error.HTTPError as exc:
        return exc.code not in DEAD_STATUSES, exc.code, str(exc)[:300]
    except ValueError as exc:  # netaisyklingas URL ([bad] ir pan.)
        return False, None, str(exc)[:300]
    except Exception as exc:  # noqa: BLE001 – tinklo klaida nėra įrodymas, kad nuotraukos nėra
        return True, None, str(exc)[:300]


class Command(BaseCommand):
    help = "Patikrina tiekėjo nuotraukų URL ir pažymi neveikiančius (--supplier, --host, --recheck-days)."

    def add_arguments(self, parser):
        parser.add_argument("--supplier", required=True)
        parser.add_argument("--host", action="append", default=[])
        parser.add_argument("--recheck-days", type=int, default=30)
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--delay", type=float, default=0.2)

    def handle(self, *args, **opts):
        urls: set[str] = set()
        for images in Offer.objects.filter(supplier__code=opts["supplier"]).values_list("images", flat=True):
            urls.update(u for u in images or [] if isinstance(u, str) and u.startswith("http"))
        if opts["host"]:
            urls = {u for u in urls if urlsplit(u).netloc in set(opts["host"])}
        fresh = timezone.now() - timedelta(days=opts["recheck_days"])
        recent = set(
            ImageUrlCheck.objects.filter(checked_at__gte=fresh, status_code__isnull=False).values_list("url", flat=True)
        )
        todo = sorted(urls - recent)
        self.stdout.write(f"urls: {len(urls)}, to check: {len(todo)}")

        def work(url: str):
            try:
                ok, status, error = probe(url)
                ImageUrlCheck.objects.update_or_create(
                    url=url,
                    defaults={"ok": ok, "status_code": status, "error": error, "checked_at": timezone.now()},
                )
                time.sleep(opts["delay"])
                return ok
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=max(1, opts["workers"])) as pool:
            results = list(pool.map(work, todo))
        dead = ImageUrlCheck.objects.filter(url__in=list(urls), ok=False).count()
        self.stdout.write(f"checked: {len(results)}, dead now: {results.count(False)}, dead total: {dead}")
