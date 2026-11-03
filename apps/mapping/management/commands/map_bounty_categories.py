from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import Category
from apps.ingestion.models import Offer
from apps.mapping.models import CategoryMapping
from apps.mapping.services import map_category
from apps.suppliers.models import Supplier

# Bounty source_path -> shop kategorijos pavadinimas (lapas).
# Konvencija pagal Korda: pop-ups/wafters/hookbait -> Boiliai,
# pellets/granules -> Peletės, groundbait/stick-mix -> Jaukai,
# skysti priedai -> Skysti kvapai, dipai -> Dipai, pasta -> Masalai.
TARGETS: dict[str, str] = {
    "BAITS > Boiled bait": "Boiliai",
    "BAITS > Soluble Boilies": "Boiliai",
    "BAITS > Granule": "Jaukai",
    "BAITS > Pellets": "Peletės",
    "BAITS > METHOD MIX": "Jaukai",
    "BAITS > STICK-MIX": "Jaukai",
    "BAITS > PREMIUM STICK-MIX": "Jaukai",
    "BAITS > PASTA": "Masalai",
    "BAITS > Knockout Activator": "Skysti kvapai",
    "BAITS > Liquids and dips > CSL": "Skysti kvapai",
    "BAITS > Liquids and dips > DOPING": "Skysti kvapai",
    "BAITS > Liquids and dips > FRESH XS": "Skysti kvapai",
    "BAITS > Liquids and dips > Liquids": "Skysti kvapai",
    "BAITS > Liquids and dips > MOKALKA": "Dipai",
    "HOOKBAITS > FLUORO HOOKBAIT KOLOBOK": "Masalai",
    "HOOKBAITS > POP-UPs > POP-UP CLASSIC": "Masalai",
    "HOOKBAITS > POP-UPs > POP-UP KRUASAN 8/10": "Masalai",
    "HOOKBAITS > POP-UPs > POP-UP MINI KRUASAN 6/8mm": "Masalai",
    "HOOKBAITS > POP-UPs > POP-UP MINI TIC-TAC": "Masalai",
    "HOOKBAITS > POP-UPs > POP-UP MULTICOLOR MULTISIZE": "Masalai",
    "HOOKBAITS > POP-UPs > POP-UP Tic-Tac": "Masalai",
    "HOOKBAITS > PREMIUM HOOKBAIT BITURBO": "Masalai",
    "HOOKBAITS > PREMIUM HOOKBAIT BITURBO IRON NEW": "Masalai",
    "HOOKBAITS > PREMIUM HOOKBAIT DOUBLE STRIKE NEW": "Masalai",
    "HOOKBAITS > PREMIUM HOOKBAIT FLUORO BITURBO": "Masalai",
    "HOOKBAITS > Soluble Wafters JOKER": "Masalai",
    "HOOKBAITS > Wafters Crucian Killer NEW!": "Masalai",
}


class Command(BaseCommand):
    help = "Sumapina Bounty Fishing tiekėjo kategorijas ir peržiūri offer'ius."

    def handle(self, *args, **options):
        supplier = Supplier.objects.get(code="bounty_fishing")

        targets: dict[str, Category] = {}
        missing: list[str] = []
        for source_path, leaf_name in TARGETS.items():
            category = Category.objects.filter(name__iexact=leaf_name).order_by("pk").first()
            if category is None:
                missing.append(leaf_name)
                continue
            targets[source_path] = category
        if missing:
            self.stdout.write(
                self.style.WARNING(f"Nerastos shop kategorijos: {sorted(set(missing))}")
            )

        created = updated = 0
        with transaction.atomic():
            for source_path, category in targets.items():
                _, was_created = CategoryMapping.objects.update_or_create(
                    supplier=supplier,
                    source_path=source_path,
                    defaults={"category": category},
                )
                created += was_created
                updated += not was_created
        self.stdout.write(f"CategoryMapping: {created} sukurta, {updated} atnaujinta.")

        # Offer'iu remap'inimas (category + is_published + product.category)
        offers = (
            Offer.objects.filter(supplier=supplier)
            .select_related("product")
            .iterator(chunk_size=500)
        )
        remapped = published = unpublished = products_updated = 0
        for offer in offers:
            category = map_category(supplier, offer.category_raw, offer.sub_category_raw)
            changed: list[str] = []
            if offer.category_id != (category.id if category else None):
                offer.category = category
                changed.append("category")
                remapped += 1
            new_published = bool(
                not offer.is_discontinued
                and offer.category_id
                and offer.selling_price is not None
                and offer.images
            )
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
