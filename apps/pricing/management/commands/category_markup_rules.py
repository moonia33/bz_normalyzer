from __future__ import annotations

import statistics
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import Category
from apps.ingestion.models import Offer
from apps.pricing.engine import apply_pricing
from apps.pricing.models import PriceAction, PriceRule, RoundingMode
from apps.suppliers.models import Supplier


def _median_ratio(ratios: list[Decimal]) -> Decimal:
    return Decimal(str(statistics.median(ratios))).quantize(Decimal("0.0001"))


class Command(BaseCommand):
    help = (
        "Prekėms be UVP: antkainis pagal kategorijos UVP/savikainos medianą "
        "(PriceRule only_without_rrp). Kategorijai su mažai pavyzdžių – tiekėjo mediana."
    )

    def add_arguments(self, parser):
        parser.add_argument("--supplier", required=True)
        parser.add_argument(
            "--rounding", default=RoundingMode.END_95, choices=[c for c, _ in RoundingMode.choices]
        )
        parser.add_argument("--min-samples", type=int, default=3)
        parser.add_argument("--priority", type=int, default=50)
        parser.add_argument("--apply", action="store_true", help="Įrašyti taisykles ir perskaičiuoti kainas.")

    def handle(self, *args, **opts):
        try:
            supplier = Supplier.objects.get(code=opts["supplier"])
        except Supplier.DoesNotExist as exc:
            raise CommandError(f"Nėra tiekėjo {opts['supplier']}") from exc

        offers = Offer.objects.filter(
            supplier=supplier, purchase_price__gt=0, category__isnull=False
        )
        ratios: dict[int, list[Decimal]] = defaultdict(list)
        missing: dict[int, int] = defaultdict(int)
        for category_id, purchase, rrp in offers.values_list("category_id", "purchase_price", "rrp_price"):
            if rrp is not None and rrp > 0:
                ratios[category_id].append(rrp / purchase)
            else:
                missing[category_id] += 1
        all_ratios = [r for values in ratios.values() for r in values]
        if not all_ratios:
            raise CommandError("Nėra prekių su UVP – medianos skaičiuoti nėra iš ko.")
        fallback = _median_ratio(all_ratios)

        categories = Category.objects.in_bulk(list(missing))
        plan = []
        for category_id, count in sorted(missing.items(), key=lambda kv: -kv[1]):
            samples = ratios.get(category_id, [])
            if len(samples) >= opts["min_samples"]:
                ratio, basis = _median_ratio(samples), f"kategorija n={len(samples)}"
            else:
                ratio, basis = fallback, f"tiekėjo mediana (kategorijoje n={len(samples)})"
            plan.append((categories[category_id], count, ratio, basis))

        self.stdout.write(f"Tiekėjo mediana: {fallback} (n={len(all_ratios)})")
        for category, count, ratio, basis in plan:
            self.stdout.write(f"  {category.slug:40} be UVP {count:4}  ×{ratio}  ({basis})")
        if not opts["apply"]:
            self.stdout.write("Dry-run: nieko neįrašyta (--apply).")
            return

        with transaction.atomic():
            for category, _count, ratio, _basis in plan:
                PriceRule.objects.update_or_create(
                    supplier=supplier,
                    category=category,
                    only_without_rrp=True,
                    action=PriceAction.MARKUP_PERCENT,
                    defaults={
                        "name": f"{supplier.code}: {category.slug} UVP/HEK mediana",
                        "priority": opts["priority"],
                        "is_active": True,
                        "value": ((ratio - 1) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                        "rounding": opts["rounding"],
                    },
                )
            updated = apply_pricing(Offer.objects.filter(supplier=supplier).select_related("supplier"))
        self.stdout.write(f"Taisyklių: {len(plan)}, perskaičiuota kainų: {updated}")
