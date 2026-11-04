from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.ingestion.models import Offer
from apps.suppliers.manufacturers import climax, sportex
from apps.suppliers.models import ManufacturerContent

SOURCES = {"climax": climax, "sportex": sportex}


def _series_codes(brand: str, series: str) -> list[str]:
    """Puslapis be kodų: prekės, kurių base_name/sub_category turi visus serijos žodžius."""
    wanted = climax.tokens(series) - climax.tokens(brand)
    if not wanted:
        return []
    codes = []
    for code, base, sub in Offer.objects.filter(brand_name_raw__iexact=brand).values_list(
        "external_id", "base_name", "sub_category_raw"
    ):
        if wanted <= climax.tokens(f"{base} {sub}"):
            codes.append(str(code).strip().upper())
    return sorted(codes)


class Command(BaseCommand):
    help = "Nuskaito gamintojo svetainės serijų puslapius į ManufacturerContent (aprašymai, parametrai, nuotraukos)."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, choices=sorted(SOURCES))
        parser.add_argument("--languages", default="de,en,pl")
        parser.add_argument("--delay", type=float, default=0.7)
        parser.add_argument("--limit", type=int, default=0, help="Riboti serijų puslapių skaičių.")

    def handle(self, *args, **opts):
        module = SOURCES.get(opts["source"])
        if module is None:
            raise CommandError(opts["source"])
        languages = tuple(code.strip() for code in opts["languages"].split(",") if code.strip())
        stats = {"pages": 0, "codes": 0}
        for lang, url, parsed in module.crawl(languages, opts["delay"], opts["limit"]):
            if "codes" not in parsed:
                codes = _series_codes(opts["source"], parsed["series"])
                parsed["codes"] = codes
                parsed["sections"] = [
                    {"heading": parsed["series"], "text": "", "rows": {code: {} for code in codes}}
                ]
            ManufacturerContent.objects.update_or_create(
                source=opts["source"],
                url=url,
                defaults={
                    "language": lang,
                    "series": parsed["series"][:300],
                    "description": parsed["intro"],
                    "specs": {"sections": parsed["sections"]},
                    "codes": parsed["codes"],
                    "images": parsed["images"],
                },
            )
            stats["pages"] += 1
            if lang == "de":
                stats["codes"] += len(parsed["codes"])
        self.stdout.write(" ".join(f"{k}={v}" for k, v in stats.items()))
