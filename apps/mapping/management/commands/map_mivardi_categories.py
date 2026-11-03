from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import Category
from apps.ingestion.models import Offer
from apps.mapping.models import CategoryMapping
from apps.mapping.services import map_category
from apps.suppliers.models import Supplier

# Mivardi CATEGORYTEXT (ček.) -> shop kategorijos pavadinimas.
# Konvencijos kaip Korda: pop-ups/wafters -> Boiliai, peletės -> Peletės,
# jauko mišiniai -> Jaukai, skysti priedai -> Skysti kvapai,
# hookbaits/boilies -> Boiliai, suktukai/montažai -> Karpinės sistemėlės.
TARGETS: dict[str, str] = {
    "Akční balíčky > Akční balíčky": "Dovanos",
    "Bivaky, brolly, přístřešky, deštníky > Bivaky a přístřešky": "Palapinės ir skėčiai",
    "Bivaky, brolly, přístřešky, deštníky > Brolly": "Palapinės ir skėčiai",
    "Bivaky, brolly, přístřešky, deštníky > Deštníky": "Palapinės ir skėčiai",
    "Čluny, lodičky, baterie a příslušenství > Carp scout a příslušenství": "Jaukinimo Laiveliai",
    "Čluny, lodičky, baterie a příslušenství > M-BOAT a příslušenství": "Jaukinimo Laiveliai",
    "Čluny, lodičky, baterie a příslušenství > M-CELL lithiové baterie": "Kiti prietaisai ir priedai",
    "Feeder & Match příslušenství > Amortizéry": "Feederio aksesuarai",
    "Feeder & Match příslušenství > Chem. světýlka a lanka": "Mechaniniai kibimo indikatoriai",
    "Feeder & Match příslušenství > Doplňky pro feeder a match": "Feederio aksesuarai",
    "Feeder & Match příslušenství > Krmítka method a feeder": "Šėryklėlės",
    "Feeder & Match příslušenství > Navázané udice": "Paruoštos sistemėlės",
    "Feeder & Match příslušenství > Plastový program": "Feederio aksesuarai",
    "Feeder & Match příslušenství > Síta": "Indai ir įrankiai",
    "Feeder & Match příslušenství > Splávky": "Plūdės",
    "Hlásiče a indikátory záběru > Hlásiče": "Elektroniniai kibimo indikatoriai",
    "Hlásiče a indikátory záběru > Indikátory": "Mechaniniai kibimo indikatoriai",
    "Kaprový program a doplňky > Backleads MIVARDI": "Karpiniai aksesuarai",
    "Kaprový program a doplňky > Bojky a Markery": "Karpiniai aksesuarai",
    "Kaprový program a doplňky > Hadičky a rovnátka": "Apsitraukiantys vamzdeliai",
    "Kaprový program a doplňky > Hotové návazce": "Paruoštos sistemėlės",
    "Kaprový program a doplňky > Kaprová olova": "Svareliai",
    "Kaprový program a doplňky > Klasická krmítka": "Šėryklėlės",
    "Kaprový program a doplňky > Kobry a Rakety": "Laidynės, kobros, raketos",
    "Kaprový program a doplňky > Krmné lopatky": "Laidynės, kobros, raketos",
    "Kaprový program a doplňky > Návazcová pouzdra a boxy": "Dėžutės",
    "Kaprový program a doplňky > Návazcové šňůrky a vlasce": "Karpiniai pavadėliniai valai",
    "Kaprový program a doplňky > Obratlíky a montáže": "Karpinės sistemėlės",
    "Kaprový program a doplňky > Pomůcky k montážím": "Karpinės sistemėlės",
    "Kaprový program a doplňky > PVA": "PVA",
    "Kaprový program a doplňky > Světla a čelovky": "Prožektoriai ir šviestuvai",
    "Kaprový program a doplňky > Vaky na boilies": "Dėžutės",
    "Kaprový program a doplňky > Zarážky a kroužky": "Karpinės sistemėlės",
    "Křesla, stolky, přepravní systémy > Křesla a příslušenství": "Kėdės",
    "Křesla, stolky, přepravní systémy > Přepravní systémy": "Karučiai",
    "Křesla, stolky, přepravní systémy > Stolky": "Staliukai ir spintelės",
    "Lehátka, spacáky a doplňky > Lehátka": "Gultai",
    "Lehátka, spacáky a doplňky > Spacáky a doplňky": "Miegmaišiai",
    "Navijáky > Náhradní cívky": "Atsarginės dalys",
    "Navijáky > Přední brzda": "Karpinės ritės",
    "Navijáky > Volnoběžka": "Karpinės ritės",
    "Navijáky > Zadní brzda": "Ritės dugninei žvejybai",
    "Návnady a nástrahy > Krmítkové směsi": "Jaukai",
    "Návnady a nástrahy > MagiCorn Pop Up": "Boiliai",
    "Návnady a nástrahy > Method & feeder": "Jaukai",
    "Návnady a nástrahy > Rapid boilie mixy": "Boiliai",
    "Návnady a nástrahy > Rapid boilies": "Boiliai",
    "Návnady a nástrahy > Rapid boilies ProActive": "Boiliai",
    "Návnady a nástrahy > Rapid Boostered Boilies": "Boiliai",
    "Návnady a nástrahy > Rapid Partikl": "Jaukai",
    "Návnady a nástrahy > Rapid Pasta": "Masalai",
    "Návnady a nástrahy > Rapid Pelety": "Peletės",
    "Návnady a nástrahy > Rapid Pop Up a Dumbells": "Boiliai",
    "Návnady a nástrahy > Rapid tekuté přísady": "Skysti kvapai",
    "Obaly, pouzdra, tašky, batohy > Batohy": "Kuprinės",
    "Obaly, pouzdra, tašky, batohy > Obaly na pruty": "Dėklai meškerėms",
    "Obaly, pouzdra, tašky, batohy > Speciální pouzdra": "Dėklai",
    "Obaly, pouzdra, tašky, batohy > Tašky": "Krepšiai",
    "Obaly, pouzdra, tašky, batohy > TMX - Match & Feeder": "Krepšiai",
    "Obaly, pouzdra, tašky, batohy > Transportní boxy": "Dėžutės",
    "Obaly, pouzdra, tašky, batohy > Transportní tašky": "Krepšiai",
    "Obaly, pouzdra, tašky, batohy > Vodotěsné tašky a pouzdra": "Krepšiai",
    "Oblečení a obuv > Boty a brodící kalhoty": "Avalynė",
    "Oblečení a obuv > Kalhoty a kraťasy": "Kelnės ir šortai",
    "Oblečení a obuv > Kšiltovky a čepice": "Akiniai ir kepurėlės",
    "Oblečení a obuv > Mikiny": "Džemperiai",
    "Oblečení a obuv > Oblečení a obuv": "Apranga ir avalynė",
    "Oblečení a obuv > Soupravy": "Žieminiai kostiumai",
    "Oblečení a obuv > Trička": "Marškinėliai",
    "Olova Stealth > Olova Stealth": "Svareliai",
    "Péče o úlovek > Podložky a vaničky": "Matai ir krepšiai žuvims",
    "Péče o úlovek > Váhy": "Kiti aksesuarai",
    "Péče o úlovek > Vážící saky a síťky": "Matai ir krepšiai žuvims",
    "Podběráky, tyče, vezírky > Kaprové podběráky a tyče": "Graibštai",
    "Podběráky, tyče, vezírky > Klasické skládací": "Graibštai",
    "Podběráky, tyče, vezírky > Match a feeder + tyče": "Graibštai",
    "Podběráky, tyče, vezírky > Náhradní síťky a příslušenství": "Graibštų priedai",
    "Podběráky, tyče, vezírky > Přívlačové": "Graibštai",
    "Podběráky, tyče, vezírky > Vezírky": "Matai ir krepšiai žuvims",
    "Pruty > Biče": "Plūdiniai meškerykočiai",
    "Pruty > Bolognese": "Plūdiniai meškerykočiai",
    "Pruty > Feederové": "Meškerykočiai dugninei",
    "Pruty > Feederové špičky": "Atsarginės dalys",
    "Pruty > Kaprové dvoudílné": "Karpiniai meškerykočiai",
    "Pruty > Kaprové třídílné": "Karpiniai meškerykočiai",
    "Pruty > Match": "Plūdiniai meškerykočiai",
    "Pruty > Spodové": "Karpiniai meškerykočiai",
    "Pruty > Sumcové a mořské": "Meškerykočiai šamams",
    "Pruty > Teleskopické": "Plūdiniai meškerykočiai",
    "Pruty > Vláčecí": "Spiningai",
    "Pruty > Závodní děličky": "Plūdiniai meškerykočiai",
    "Stojany, vidličky a příslušenství > Distanční vidličky a zakrmovací stojan": "Stovai ir kuoliukai",
    "Stojany, vidličky a příslušenství > Feeder a Match": "Meškerių laikikliai",
    "Stojany, vidličky a příslušenství > Rohatinky a koncovky": "Stovai ir kuoliukai",
    "Stojany, vidličky a příslušenství > Stojany": "Stovai",
    "Stojany, vidličky a příslušenství > Vidličky a hrazdy": "Stovai ir kuoliukai",
    "Vlasce, šňůry a háčky > Háčky Yamatsu": "Karpiniai kabliukai",
    "Vlasce, šňůry a háčky > Háčky M-Point": "Karpiniai kabliukai",
    "Vlasce, šňůry a háčky > Pletené šňůry": "Pinti valai",
    "Vlasce, šňůry a háčky > Vlasce - Kaprařina": "Monofilamentiniai valai",
    "Vlasce, šňůry a háčky > Vlasce - Match&Feeder": "Monofilamentiniai valai",
    "Vlasce, šňůry a háčky > Vlasce - Přívlač sumci a moře": "Jūrai, Šamams Valai",
}


class Command(BaseCommand):
    help = "Sumapina Mivardi (čekiškas) kategorijas ir peržiūri offer'ius."

    def handle(self, *args, **options):
        supplier = Supplier.objects.get(code="mivardi")

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
