"""Forgotten Flavours (Shopify) kategorijų mapinimas.

Shopify `product_type` -> LT kategorijų medis. Idempotentiška: sukuria/
atnaujina CategoryMapping ir permeta offer'ius/produktus. Po pirmo importo
`--dry-run` parodo tikras source reikšmes – papildyti MAPPINGS.

Paleidimas:  manage.py map_forgotten_flavours_categories [--dry-run]
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import Category
from apps.ingestion.models import Offer
from apps.mapping.models import CategoryMapping
from apps.mapping.services import map_category
from apps.pricing.engine import compute_selling_price
from apps.pricing.models import PriceRule
from apps.suppliers.models import Supplier

SUPPLIER_CODE = "forgotten_flavours"

# (source_category_raw, source_sub_category_raw) -> LT kategorijos path.
MAPPINGS: list[tuple[str, str, str]] = [
    # Shopify product_type reikšmės (iš products.json, 2026-11).
    ("High attract hookbaits Forgotten Flavours", "", "Masalai ir jaukai > Boiliai ir peletės > Boiliai"),
    ("End tackle", "", "Suktukai, stabdžiai, gumytės > Karpiniai aksesuarai"),
    ("Hooks", "", "Kabliukai > Karpiniai kabliukai"),
    ("Apparel", "", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė"),
]

LEAVE_UNMAPPED = {("", "")}


def _category_by_path(path: str) -> Category | None:
    parts = [p.strip() for p in path.split(">") if p.strip()]
    if not parts:
        return None
    candidates = Category.objects.filter(name__iexact=parts[-1]).select_related(
        "parent__parent__parent"
    )
    matches = [c for c in candidates if c.path().lower() == path.lower()]
    if matches:
        return sorted(matches, key=lambda c: c.pk)[0]
    return None


class Command(BaseCommand):
    help = (
        "Forgotten Flavours CategoryMapping + offer'ių/product'ų "
        "kategorijų ir is_published permapping'as."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        supplier = Supplier.objects.get(code=SUPPLIER_CODE)

        offer_counts = Counter(
            Offer.objects.filter(supplier=supplier).values_list(
                "category_raw", "sub_category_raw"
            )
        )
        total_offers = sum(offer_counts.values())

        targets: dict[tuple[str, str], Category] = {}
        errors: list[str] = []
        for source_cat, source_sub, target_path in MAPPINGS:
            category = _category_by_path(target_path)
            if category is None:
                errors.append(f"Kategorija nerasta: '{target_path}'")
                continue
            targets[(source_cat, source_sub)] = category
        if errors:
            for err in errors:
                self.stderr.write(self.style.ERROR(err))
            raise CommandError("Nepavyko rezoliuoti visų tikslinių kategorijų.")

        self.stdout.write("source -> target (offers):")
        mapped_offers = 0
        for (source_cat, source_sub), category in sorted(
            targets.items(), key=lambda kv: -offer_counts.get((kv[0][0], kv[0][1]), 0)
        ):
            count = offer_counts.get((source_cat, source_sub), 0)
            mapped_offers += count
            self.stdout.write(
                f"  {count:>5}  {source_cat} | {source_sub}  ->  {category.path()}"
            )
        self.stdout.write("\nBe map'inimo:")
        for (source_cat, source_sub), count in sorted(
            (c for c in offer_counts.items() if c[0] not in targets),
            key=lambda kv: -kv[1],
        ):
            self.stdout.write(f"  {count:>5}  {source_cat} | {source_sub}")
        self.stdout.write(f"\nCoverage: {mapped_offers}/{total_offers}")

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: DB nepaliesta."))
            return

        created = updated = 0
        with transaction.atomic():
            for (source_cat, source_sub), category in targets.items():
                _, was_created = CategoryMapping.objects.update_or_create(
                    supplier=supplier,
                    source_category_raw=source_cat,
                    source_sub_category_raw=source_sub,
                    defaults={"category": category},
                )
                created += was_created
                updated += not was_created
        self.stdout.write(f"CategoryMapping: {created} sukurta, {updated} atnaujinta.")

        remapped = products_updated = 0
        rules = list(PriceRule.objects.filter(is_active=True))
        for offer in (
            Offer.objects.filter(supplier=supplier)
            .select_related("product")
            .iterator(chunk_size=500)
        ):
            category = map_category(supplier, offer.category_raw, offer.sub_category_raw)
            changed: list[str] = []
            if offer.category_id != (category.id if category else None):
                offer.category = category
                changed.append("category")
                remapped += 1
            new_price = compute_selling_price(offer, rules)
            if offer.selling_price != new_price:
                offer.selling_price = new_price
                changed.append("selling_price")
            new_published = bool(
                not offer.is_discontinued
                and offer.category_id
                and offer.selling_price is not None
                and offer.images
            )
            if offer.is_published != new_published:
                offer.is_published = new_published
                changed.append("is_published")
            if changed:
                offer.save(update_fields=[*changed, "updated_at"])
            product = offer.product
            if product is not None and offer.category_id and product.category_id != offer.category_id:
                product.category_id = offer.category_id
                product.save(update_fields=["category", "updated_at"])
                products_updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Offers remapped: {remapped}; products category set: {products_updated}."
            )
        )
