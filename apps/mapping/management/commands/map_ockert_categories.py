from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import Category
from apps.ingestion.models import Offer
from apps.mapping.models import CategoryMapping
from apps.mapping.services import map_category
from apps.suppliers.models import Supplier

# Ockert kategorijos kelias (DE) -> shop kategorijos slug.
# 'Auslauf Ruten > <tipas>' – tipą iš pavadinimo nustato adapteris.
TARGETS: dict[str, str] = {
    "Angelschnüre > Geflochtene Schnüre > X8": "pinti-valai",
    "Angelschnüre > Geflochtene Schnüre > X8 Neverfade": "pinti-valai",
    "Angelschnüre > Geflochtene Schnüre > X8 Neverfade Baitcast": "pinti-valai",
    "Angelschnüre > Geflochtene Schnüre > X4": "pinti-valai",
    "Angelschnüre > Geflochtene Schnüre > Seamaster Braid": "pinti-valai",
    "Angelschnüre > Geflochtene Schnüre > Catfish Braid": "pinti-valai",
    "Angelschnüre > Monofile Schnüre > Strylon - das Kraftmonofil": "monofilamentiniai-valai",
    "Angelschnüre > Monofile Schnüre > Edeltrout Forellenschnur": "monofilamentiniai-valai",
    "Angelschnüre > Cult Carp > Cult Carp Mono": "monofilamentiniai-valai",
    "Angelschnüre > Fluorocarbon & HardMono": "fluorokarbono-pavadelinis-valas",
    "Angelschnüre > Seamaster": "jurai-samams-valai",
    "Angelschnüre > Cult Catfish": "samu-aksesuarai",
    "Angelschnüre > Ultra Raubfisch": "pavadeliai-spiningavimui",
    "Angelschnüre > Cult Feeder": "valai-ir-pavadeliai",
    "Angelschnüre > Cult Carp > Cult Carp Braided & Coated Leader": "karpiniai-pavadeliniai-valai",
    "Angelschnüre > Cult Carp > Cult Carp Lead(free) Core": "karpiniai-pavadeliniai-valai",
    "Angelschnüre > Cult Carp > Cult Carp Mono Leader": "karpiniai-pavadeliniai-valai",
    "Angelschnüre > Cult Carp > Cult Carp Zubehör": "karpiniai-aksesuarai",
    "Kryston > Score Leadcores": "karpiniai-pavadeliniai-valai",
    "Kryston > Ready Made Rigs": "paruostos-sistemeles",
    "Kryston > Terminal Tackle": "karpiniai-aksesuarai",
    "Kryston > Tungsten & Bogey": "karpiniai-aksesuarai",
    "Kryston > Taschen & Bekleidung": "apranga-ir-avalyne",
    "Sportex Bekleidung > Sportex T-Shirts & Hoodies & Function-wear": "marskineliai",
    "Sportex Bekleidung > Caps und Beanies": "akiniai-ir-kepureles",
    "TICA Angelrollen > Spinnrollen": "rites-spiningavimui",
    "TICA Angelrollen > Flash Carbon Rollen": "rites-spiningavimui",
    "TICA Angelrollen > Karpfenrollen": "karpines-rites",
    "TICA Angelrollen > Meeresrollen": "jurines-rites",
    "Sportex Ruten > Feeder Ruten > Rapid Feeder RS-2 Serie": "meskerykociai-dugninei",
    "Sportex Ruten > Reise Ruten > Spinn-Reiseruten": "spiningai",
    "Sportex Ruten > Salmon Ruten": "spiningai",
    "Sportex Ruten > Auslauf Ruten > Spinnruten": "spiningai",
    "Sportex Ruten > Auslauf Ruten > Karpfenruten": "karpiniai-meskerykociai",
    "Sportex Ruten > Auslauf Ruten > Fliegenruten": "muselines-meskerykociai",
    "Sportex Ruten > Auslauf Ruten > Meeresruten": "juriniai-meskerykociai",
    "Sportex Ruten > Auslauf Ruten > Wallerruten": "meskerykociai-samams",
    "Sportex Ruten > Auslauf Ruten > Feederruten": "meskerykociai-dugninei",
    "Sportex Ruten > Auslauf Ruten > Trollingruten": "velkiavimui-meskerykociai",
    "Fly Fishing > Fly Backing & Accessories": "valai-ir-pavadeliai",
}


class Command(BaseCommand):
    help = (
        "Sumapina Ockert (vokiškas) kategorijas ir peržiūri offer'ius. "
        "Auslauf meškerės gauna tipą tik po katalogo perimportavimo."
    )

    def handle(self, *args, **options):
        supplier = Supplier.objects.get(code="ockert")

        targets: dict[str, Category] = {}
        missing: list[str] = []
        for source_path, slug in TARGETS.items():
            category = Category.objects.filter(slug=slug).order_by("pk").first()
            if category is None:
                missing.append(slug)
                continue
            targets[source_path] = category
        if missing:
            self.stdout.write(self.style.WARNING(f"Nerastos shop kategorijos: {sorted(set(missing))}"))

        created = updated = 0
        with transaction.atomic():
            for source_path, category in targets.items():
                # Kaip importas: category_raw = visas kelias, sub = paskutinė dalis.
                _, was_created = CategoryMapping.objects.update_or_create(
                    supplier=supplier,
                    source_path=source_path,
                    defaults={"category": category},
                    create_defaults={
                        "category": category,
                        "source_category_raw": source_path,
                        "source_sub_category_raw": source_path.rsplit(" > ", 1)[-1],
                    },
                )
                created += was_created
                updated += not was_created
        self.stdout.write(f"CategoryMapping: {created} sukurta, {updated} atnaujinta.")

        remapped = published = unpublished = products_updated = 0
        offers = Offer.objects.filter(supplier=supplier).select_related("product").iterator(chunk_size=500)
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
            if product is not None and offer.category_id and product.category_id != offer.category_id:
                product.category_id = offer.category_id
                product.save(update_fields=["category", "updated_at"])
                products_updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Offers remapped: {remapped}; is_published +{published}/-{unpublished}; "
                f"products category set: {products_updated}."
            )
        )
