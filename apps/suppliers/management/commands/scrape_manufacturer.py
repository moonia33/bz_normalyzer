from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.ingestion.models import Offer
from apps.suppliers.manufacturers import climax, sportex, zuklys
from apps.suppliers.manufacturers.brands import BRANDS, BrandSource
from apps.suppliers.models import ManufacturerContent

SOURCES = {"climax": climax, "sportex": sportex, "zuklys": zuklys, **BRANDS}


def _series_codes(brand: str, series: str) -> list[str]:
    """Puslapis be kodų: prekės, kurių base_name/sub_category turi visus serijos žodžius."""
    wanted = climax.tokens(series) - climax.tokens(brand)
    if not wanted:
        return []
    codes = []
    for code, base, sub in Offer.objects.filter(
        brand_name_raw__iexact=brand
    ).values_list("external_id", "base_name", "sub_category_raw"):
        if wanted <= climax.tokens(f"{base} {sub}"):
            codes.append(str(code).strip().upper())
    return sorted(codes)


def _zuklys_codes(path: str | None) -> list[str]:
    """Albatros kodai, kuriems dar nėra jokio turinio (gamintojo ar zuklys)."""
    if path:
        with open(path, encoding="utf-8") as fh:
            return [line.strip().upper() for line in fh if line.strip()]
    covered = set()
    for codes in ManufacturerContent.objects.exclude(codes=[]).values_list("codes", flat=True):
        covered.update(codes)
    codes = Offer.objects.filter(supplier__code="albatros").values_list("external_id", flat=True)
    return sorted({str(c).strip().upper() for c in codes} - covered)


def _keep_most_specific(source: str, urls: set[str]) -> int:
    """Prekė priskiriama tik tiksliausiai serijai (daugiausia sutampančių žodžių).

    Lyginami tik šio paleidimo atnaujinti puslapiai (`urls`), kad seni sąrašai nepersvertų naujų."""
    dropped = 0
    for language in (
        ManufacturerContent.objects.filter(source=source)
        .values_list("language", flat=True)
        .distinct()
    ):
        rows = list(
            ManufacturerContent.objects.filter(
                source=source, language=language, url__in=urls
            )
        )
        best: dict[str, int] = {}
        for row in rows:
            size = (row.specs or {}).get("series_tokens", 0)
            for code in row.codes:
                best[code] = max(best.get(code, 0), size)
        for row in rows:
            size = (row.specs or {}).get("series_tokens", 0)
            keep = [code for code in row.codes if best[code] == size]
            if len(keep) != len(row.codes):
                dropped += len(row.codes) - len(keep)
                row.codes = keep
                for section in row.specs.get("sections", []):
                    section["rows"] = {
                        c: v
                        for c, v in (section.get("rows") or {}).items()
                        if c in keep
                    }
                row.save(update_fields=["codes", "specs", "updated_at"])
    return dropped


class Command(BaseCommand):
    help = "Nuskaito gamintojo svetainės serijų puslapius į ManufacturerContent (aprašymai, parametrai, nuotraukos)."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, choices=sorted(SOURCES))
        parser.add_argument("--languages", default="de,en,pl")
        parser.add_argument("--delay", type=float, default=0.7)
        parser.add_argument(
            "--limit", type=int, default=0, help="Riboti serijų puslapių skaičių."
        )
        parser.add_argument(
            "--codes-file",
            help="zuklys: kodų sąrašas (po vieną eilutėje); be jo – tiekėjo albatros kodai be turinio.",
        )

    def handle(self, *args, **opts):
        module = SOURCES.get(opts["source"])
        if module is None:
            raise CommandError(opts["source"])
        languages = tuple(
            code.strip() for code in opts["languages"].split(",") if code.strip()
        )
        stats = {"pages": 0, "codes": 0}
        seen: set[str] = set()
        codes_seen: set[str] = set()
        if module is zuklys:
            pages = zuklys.crawl(_zuklys_codes(opts.get("codes_file")), opts["delay"], opts["limit"])
        else:
            pages = module.crawl(languages, opts["delay"], opts["limit"])
        for lang, url, parsed in pages:
            if "codes" not in parsed:
                codes = _series_codes(
                    opts["source"], parsed.get("series_de") or parsed["series"]
                )
                parsed["series_tokens"] = len(
                    climax.tokens(parsed.get("series_de") or parsed["series"])
                )
                parsed["codes"] = codes
                parsed["sections"] = [
                    {
                        "heading": parsed["series"],
                        "text": "",
                        "rows": {code: {} for code in codes},
                    }
                ]
            ManufacturerContent.objects.update_or_create(
                source=opts["source"],
                url=url,
                defaults={
                    "language": lang,
                    "series": parsed["series"][:300],
                    "description": parsed["intro"],
                    "specs": {
                        "sections": parsed["sections"],
                        "series_tokens": parsed.get("series_tokens", 0),
                        **({"eans": parsed["eans"]} if parsed.get("eans") else {}),
                        **(
                            {"category": parsed["category"]}
                            if parsed.get("category")
                            else {}
                        ),
                    },
                    "codes": parsed["codes"],
                    "images": parsed["images"],
                },
            )
            seen.add(url)
            stats["pages"] += 1
            codes_seen.update(parsed["codes"])
        stats["codes"] = len(codes_seen)
        if isinstance(module, BrandSource) and not opts["limit"]:
            stale = ManufacturerContent.objects.none()
            for prefix in module.complete_prefixes:
                stale |= ManufacturerContent.objects.filter(
                    source=opts["source"], url__startswith=prefix
                )
            stats["stale"] = (
                stale.exclude(url__in=seen).exclude(codes=[]).update(codes=[])
            )
        if module is climax:
            if not opts["limit"]:
                stats["stale"] = (
                    ManufacturerContent.objects.filter(source=opts["source"])
                    .exclude(url__in=seen)
                    .exclude(codes=[])
                    .update(codes=[])
                )
            stats["dropped"] = _keep_most_specific(opts["source"], seen)
        self.stdout.write(" ".join(f"{k}={v}" for k, v in stats.items()))
