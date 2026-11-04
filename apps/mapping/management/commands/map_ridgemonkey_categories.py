"""RidgeMonkey (B2B nopCommerce) kategorijų mapinimas.

B2B breadcrumb: 'Terminal Tackle > Hooks' / 'Clothing > Clothing > Classic' /
'Bankside Accommodation'. Idempotentiškai sukuria CategoryMapping eilutes
ir permeta esamus offer'ius/produktus (category + is_published).

Paleidimas:  manage.py map_ridgemonkey_categories [--dry-run]
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import Category
from apps.ingestion.models import Offer
from apps.mapping.models import CategoryMapping
from apps.mapping.services import map_category
from apps.suppliers.models import Supplier

SUPPLIER_CODE = "ridgemonkey"

# (source_category_raw, source_sub_category_raw) -> LT kategorijos path.
# source_category_raw = pirmas breadcrumb narys, sub = likęs kelias.
MAPPINGS: list[tuple[str, str, str]] = [
    # ---- Terminal Tackle (Connexion) ----
    ("Terminal Tackle", "Hooks", "Kabliukai > Karpiniai kabliukai"),
    ("Terminal Tackle", "Hooklinks", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai > Karpiniai pavadėliniai valai"),
    ("Terminal Tackle", "Leaders", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai"),
    ("Terminal Tackle", "Rock Bottom", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai"),
    ("Terminal Tackle", "Mainlines", "Valai ir pavadėliai"),
    ("Terminal Tackle", "Weights", "Svareliai ir šeryklėlės > Svareliai"),
    ("Terminal Tackle", "Disperse PVA", "Masalai ir jaukai > PVA"),
    ("Terminal Tackle", "Fixed Marker Systems", "Kibimo indikatoriai ir plūdės > Plūdės"),
    ("Terminal Tackle", "Zyggo", "Masalai ir jaukai"),
    ("Terminal Tackle", "Bait Attachment", "Suktukai, stabdžiai, gumytės > Karpiniai aksesuarai"),
    ("Terminal Tackle", "Kickers", "Suktukai, stabdžiai, gumytės > Karpiniai aksesuarai"),
    ("Terminal Tackle", "Metalware", "Suktukai, stabdžiai, gumytės"),
    ("Terminal Tackle", "Sleeves & Beads", "Suktukai, stabdžiai, gumytės"),
    ("Terminal Tackle", "Needles & Tools", "Drabužiai, Palapinės, Baldai, Įrankiai > Indai ir įrankiai"),
    # ---- Accessories ----
    ("Accessories", "Bankside Accommodation", "Drabužiai, Palapinės, Baldai, Įrankiai > Baldai Žvejams"),
    ("Accessories", "Baiting & Storage", "Kiti aksesuarai"),
    ("Accessories", "Other Accessories", "Kiti aksesuarai"),
    # ---- Clothing ----
    ("Clothing", "Clothing > Classic", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė"),
    ("Clothing", "Clothing", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė"),
    ("Clothing", "Eyewear", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Akiniai ir kepurėlės"),
    ("Clothing", "Footwear", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Avalynė"),
    ("Clothing", "Headwear", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Akiniai ir kepurėlės"),
    ("Clothing", "Socks", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė"),
    # ---- Kiti root'ai ----
    ("Cookware", "", "Indai"),
    ("Ruggage", "", "Krepšiai, dėklai, dėžutės > Krepšiai"),
    ("Luggage", "", "Krepšiai, dėklai, dėžutės > Krepšiai"),
    ("Power & Lighting", "", "Elektronika"),
    ("ProCare", "", "Krepšiai, dėklai, dėžutės > Matai ir krepšiai žuvims"),
]

LEAVE_UNMAPPED = {
    ("New Products", ""),   # mišrus naujienų konteineris
    ("", ""),
}


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
        "RidgeMonkey CategoryMapping eilutės (EN -> LT kategorijų medis) + "
        "esamų offer'ių/product'ų kategorijų ir is_published permapping'as."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Spausdina planą, nieko nerašo į DB.",
        )

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
            targets.items(),
            key=lambda kv: -offer_counts.get((kv[0][0], kv[0][1]), 0),
        ):
            count = offer_counts.get((source_cat, source_sub), 0)
            mapped_offers += count
            marker = "" if count else "  (no offers)"
            self.stdout.write(
                f"  {count:>5}  {source_cat} | {source_sub}  ->  {category.path()}{marker}"
            )
        unmapped = {
            combo: n for combo, n in offer_counts.items() if combo not in targets
        }
        self.stdout.write("\nSąmoningai nemap'inama / be eilutės:")
        for (source_cat, source_sub), count in sorted(unmapped.items(), key=lambda kv: -kv[1]):
            flag = " (LEAVE_UNMAPPED)" if (source_cat, source_sub) in LEAVE_UNMAPPED else ""
            self.stdout.write(f"  {count:>5}  {source_cat} | {source_sub}{flag}")
        self.stdout.write(
            f"\nCoverage: {mapped_offers}/{total_offers} "
            f"= {mapped_offers / total_offers * 100:.1f}% offer'ių"
        )

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

        offers = (
            Offer.objects.filter(supplier=supplier)
            .select_related("product")
            .iterator(chunk_size=500)
        )
        remapped = published = unpublished = products_updated = 0
        for offer in offers:
            category = map_category(
                supplier, offer.category_raw, offer.sub_category_raw
            )
            changed: list[str] = []
            if offer.category_id != (category.id if category else None):
                offer.category = category
                changed.append("category")
                remapped += 1
            if (
                not offer.is_discontinued
                and offer.category_id
                and offer.selling_price is not None
                and offer.images
            ):
                new_published = True
            else:
                new_published = False
            if offer.is_published != new_published:
                offer.is_published = new_published
                changed.append("is_published")
                published += new_published
                unpublished += not new_published
            if changed:
                offer.save(update_fields=[*changed, "updated_at"])
            product = offer.product
            if (
                product is not None
                and offer.category_id
                and product.category_id != offer.category_id
            ):
                product.category_id = offer.category_id
                product.save(update_fields=["category", "updated_at"])
                products_updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Offers remapped: {remapped}; is_published +{published}/-{unpublished}; "
                f"products category set: {products_updated}."
            )
        )
