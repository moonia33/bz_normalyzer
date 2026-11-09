from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import Category
from apps.ingestion.models import Offer
from apps.mapping.models import CategoryMapping
from apps.mapping.services import map_category
from apps.suppliers.models import Supplier

# Albatros kategorijos kelias -> shop kategorijos slug (ne kelias: 'Vandens Sportas > Kitos prekės'
# yra dvi kategorijos). Stock kodai su nemus.lt puslapiu gauna nemus skyrių (adapteris),
# kiti – Stock skyrių; nemus-only be skyriaus – 'Nemus'.
NEMUS: dict[str, str] = {
    "Aksesuarai > Borika FASTen laikikliai": "laivu-laikikliai",
    "Aksesuarai > Priedai": "laivu-priedai",
    "Aksesuarai > Ram Laikikliai": "laivu-laikikliai",
    "Ram Laikikliai": "laivu-laikikliai",
    "Akumuliatoriai ir pakrovėjai": "akumuliatoriai-ir-pakrovejai",
    "Gamintojas > Energy Research": "akumuliatoriai-ir-pakrovejai",
    "Gamintojas > Rebelcell": "akumuliatoriai-ir-pakrovejai",
    "Gamintojas > Roy Pow": "akumuliatoriai-ir-pakrovejai",
    "Audio sistemos > Hertz": "audio-sistemos",
    "Gamintojas > Minnkota": "valciu-varikliai",
    "Echolotai": "echolotai",
    "Echolotai > Echolotų priedai": "echolotu-priedai",
    "Echolotai > Echolotų priedai > Ethernet tinklo įranga": "ethernet-tinklo-iranga",
    "Echolotai > Echolotų priedai > Kiti priedai": "kiti-echolotu-priedai",
    "Echolotai > Echolotų priedai > Sonarai": "sonarai",
    "Echolotai > Humminbird echolotai": "echolotai",
    "Echolotai > Minn Kota Echolotai": "echolotai",
    "Elektriniai varikliai > Elektrinių variklių priedai": "elektriniu-varikliu-priedai",
    "Elektriniai varikliai > Elektrinių variklių priedai > Propeleriai el. varikliams": "propeleriai",
    "Elektriniai varikliai > Elektrinių variklių priedai > Tvirtinimo ir montavimo įranga": "tvirtinimo-ir-montavimo-iranga",
    "Elektriniai varikliai > Elektrinių variklių priedai > Valdymo įranga": "valdymo-iranga",
    "Elektriniai varikliai > Gale pakabinami varikliai > Minnkota Endura C2": "gale-pakabinami-varikliai",
    "Elektriniai varikliai > Gale pakabinami varikliai > Minnkota Endura Max": "gale-pakabinami-varikliai",
    "Elektriniai varikliai > Gale pakabinami varikliai > Minnkota Traxxis": "gale-pakabinami-varikliai",
    "Elektriniai varikliai > Priekyje tvirtinami varikliai > Minnkota Instinct": "priekyje-tvirtinami-varikliai",
    "Elektriniai varikliai > Priekyje tvirtinami varikliai > Minnkota Powerdrive": "priekyje-tvirtinami-varikliai",
    "Elektriniai varikliai > Priekyje tvirtinami varikliai > Minnkota Terrova": "priekyje-tvirtinami-varikliai",
    "Elektriniai varikliai > Priekyje tvirtinami varikliai > Minnkota Ulterra": "priekyje-tvirtinami-varikliai",
    "Kajakai": "kajakai",
    "Kajakai > Old Town": "kajakai",
    "Vandens pramogos > Kajakai": "kajakai",
    "Pramoginiai laivai > AUTUMN 2026 CAMPAIGN": "pramoginiai-laivai",
    "Pramoginiai laivai > Bayliner": "pramoginiai-laivai",
    "Pramoginiai laivai > XO": "pramoginiai-laivai",
    "Žvejybiniai kateriai > Alba boats": "zvejybiniai-kateriai",
    "Žvejybiniai kateriai > Alumagic": "zvejybiniai-kateriai",
    "Žvejybiniai kateriai > Skylla": "zvejybiniai-kateriai",
    "Vandens pramogos > Apranga ir avalynė": "apranga-bei-avalyne",
    "Vandens pramogos > Hidrokostiumai": "hidrokostiumai",
    "Vandens pramogos > Hidrokostiumai > Moteriški hidrokostiumai": "moteriski-hidrokostiumai",
    "Vandens pramogos > Hidrokostiumai > Vaikiški hidrokostiumai": "vaikiski-hidrokostiumai",
    "Vandens pramogos > Hidrokostiumai > Vyriški hidrokostiumai": "vyriski-hidrokostiumai",
    "Vandens pramogos > Liemenės": "liemenes",
    "Vandens pramogos > Liemenės > Moteriškos liemenės": "moteriskos-liemenes",
    "Vandens pramogos > Liemenės > Vaikiškos liemenės": "vaikiskos-liemenes",
    "Vandens pramogos > Liemenės > Vyriškos liemenės": "vyriskos-liemenes",
    "Vandens pramogos > Irklentės": "irklentes",
    "Vandens pramogos > Priedai vandens sportui": "aksesuarai",
    "Vandens pramogos > Vandens atrakcionai": "vandens-atrakcionai",
    "Vandens pramogos > Wake lentos ir vandens slidės": "vandenlentes",
    "Vandens pramogos > Wake lentos ir vandens slidės > Vandens slidės": "vandens-slides",
    "Nemus": "nepriskirta",
}
STOCK: dict[str, str] = {
    "BORIKA": "laivu-laikikliai",
    "RAM Mounts": "laivu-laikikliai",
    "NEXO - MARINAC Boat Accessories": "laivu-priedai",
    "JOBE Accessories Clothing Footwear": "apranga-bei-avalyne",
    "JOBE Life VESTS": "liemenes",
    "JOBE Wetsuits": "hidrokostiumai",
    "JOBE Towables": "vandens-atrakcionai",
    "JOBE SUP": "irklentes",
    "JOBE WakeBoards WaterSkis": "vandenlentes",
    "KAYAK": "kajakai",
    "Humminbird Accessories": "echolotu-priedai",
    "Humminbird Depthfinders": "echolotai",
    "Maps": "echolotai",
    "Minn Kota Accessories": "elektriniu-varikliu-priedai",
    "Minn Kota Motors": "valciu-varikliai",
    "Boating/Motors": "valciu-varikliai",
    "REBELCELL": "akumuliatoriai-ir-pakrovejai",
    "RoyPow": "akumuliatoriai-ir-pakrovejai",
    "Energy-Research B.V.": "akumuliatoriai-ir-pakrovejai",
    "HERTZ Audio": "audio-sistemos",
}
# Stock skyriai -> esamos kategorijos; "> pogrupis" – adapterio SECTION_SPLITS.
FISHING: dict[str, str] = {
    "Bait / Crazy Fish": "guminukai",
    "Bait / Wobbler": "vobleriai",
    "Bait / Soft Lures": "guminukai",
    "Bait / Soft Lures BULK BOX - Dėžutėse": "guminukai",
    "Bait / Sea Lures - Jūrinės gumos": "guminukai",
    "Bait / MEPPS": "sukriukes",
    "Bait / MEPPS > Blizgės": "blizges",
    "Bait / Spinner": "sukriukes",
    "Bait / Spinnerbait - Dvigubos sukrės": "sukriukes",
    "Bait / RAPALA": "vobleriai",
    "Bait / RAPALA > Blizgės": "blizges",
    "Bait / RAPALA > Balansyrai": "balansyrai",
    "Bait / Spoon - Blizgė": "blizges",
    "Bait / Sea Jig - Pilkeriai": "blizges",
    "Bait/Winter - Žieminiai masalai": "balansyrai",
    "Bait / Hybride Lures - Hibridiniai masalai": "spiningui",
    "Bait / Sea Rig - Jūrinė sistema": "paruostos-sistemeles",
    "Bait / Carp PVA": "jaukai",
    "Bait / Carp PVA > Peletės": "peletes",
    "CAT Fishing - Šamų žvejyba": "masalai-samams",
    "CAT Fishing - Šamų žvejyba > Meškerykočiai": "meskerykociai-samams",
    "CAT Fishing - Šamų žvejyba > Valai": "jurai-samams-valai",
    "CAT Fishing - Šamų žvejyba > Kabliukai": "kabliukai-samams",
    "CAT Fishing - Šamų žvejyba > Aksesuarai": "samu-aksesuarai",
    "LEAD / JIG HEAD": "galvakabliai",
    "LEAD / JIG HEAD > Svareliai": "svareliai",
    "HOOKS - KABLIUKAI": "kabliukai-spiningavimui",
    "HOOKS - KABLIUKAI > Šamams": "kabliukai-samams",
    "Lure Accessories / Stingers": "kabliukai-spiningavimui",
    "Rod / Spin - Kotas": "spiningai",
    "Rod / Casting": "spiningai",
    "Rod Travel Spin / Tele Spin": "spiningai",
    "Rod / Sea": "juriniai-meskerykociai",
    "Rod / Sea > Velkiavimui": "velkiavimui",
    "Rod / Surf": "juriniai-meskerykociai",
    "Rod / Catfish - Šamams": "meskerykociai-samams",
    "Rod / Carp": "karpiniai-meskerykociai",
    "Rod / Feeder": "meskerykociai-dugninei",
    "Rod / Tele": "pludiniai-meskerykociai",
    "Rod / Tele > Spiningai": "spiningai",
    "Rod / Match": "pludiniai-meskerykociai",
    "Rod / Fly": "muselines-meskerykociai",
    "Reel - Ritė": "rites-spiningavimui",
    "Reel - Ritė > Dugninei": "rites-dugninei-zvejybai",
    "Reel / Multi Casting": "rites-spiningavimui",
    "Reel / Longcast": "karpines-rites",
    "Reel / Longcast > Spodinės": "spodines-rites",
    "Reel / Longcast > Dugninei": "rites-dugninei-zvejybai",
    "Reel / BaitFeeder": "karpines-rites",
    "Reel / Multi Sea": "jurines-rites",
    "Reel / Multi II-speed": "jurines-rites",
    "Reel / Fly": "muselines",
    "Winter Fishing Reels&Rods": "ziemines-rites",
    "Winter Fishing Reels&Rods > Meškerytės": "meskerytes",
    "Mono Line - Valas": "monofilamentiniai-valai",
    "Braided Line - Pintas valas": "pinti-valai",
    "Fluoro Carbon Line": "fluorokarbono-pavadelinis-valas",
    "Line/Sea - Jūrinis valas": "jurai-samams-valai",
    "Line / Carp, Leader - Karpinis valas, pavadėliai": "monofilamentiniai-valai",
    "Line / Carp, Leader - Karpinis valas, pavadėliai > Pavadėliai": "karpiniai-pavadeliniai-valai",
    "Line/Fly - Muselinis valas": "valai-ir-pavadeliai",
    "Line/Fly - Muselinis valas > Konusiniai": "konusinis-valas",
    "Traces / Leaders / Crimps - Pavadėliai / Viela / Užspaudėj": "pavadeliai-spiningavimui",
    "Traces / Leaders / Crimps - Pavadėliai / Viela / Užspaudėj > Metaliniai": "metaliniai-pavadeliai",
    "Snaps / Swivels / Rings - Karabinai / Žiedeliai": "suktukai",
    "FEEDER SYSTEM": "feederio-aksesuarai",
    "FEEDER SYSTEM > Sistemėlės": "paruostos-sistemeles",
    "FEEDER SYSTEM > Laikikliai": "meskeriu-laikikliai",
    "FEEDER SYSTEM > Šėryklėlės": "serykleles",
    "Carp Hooks / Rigs - Carp kabliukai / su pavadėliu": "karpiniai-kabliukai",
    "Carp Hooks / Rigs - Carp kabliukai / su pavadėliu > Sistemėlės": "karpines-sistemeles",
    "Carp rig accesories - Carp sistemėlių elementai": "karpiniai-aksesuarai",
    "Carp Tools, Spombs, Markers - Karpiniai įrankiai": "karpiniai-aksesuarai",
    "Carp Tools, Spombs, Markers - Karpiniai įrankiai > Laidynės": "laidynes-kobros-raketos",
    "Carp Landing Net, MAT, Carp Care": "matai-ir-krepsiai-zuvims",
    "Carp Landing Net, MAT, Carp Care > Graibštai": "graibstai",
    "Bag / Box - Krepšys / dėžė": "krepsiai",
    "Bag / Box - Krepšys / dėžė > Dėžutės": "dezutes",
    "Tackle Storage - Dėžė": "dezutes",
    "Rod Bag - Dėklai meškerėms": "deklai-meskerems",
    "ROD POD - STOVAI - ROD HOLDERS, ROD REST": "stovai",
    "ROD POD - STOVAI - ROD HOLDERS, ROD REST > Kuoliukai": "stovai-ir-kuoliukai",
    "ROD POD - STOVAI - ROD HOLDERS, ROD REST > Laikikliai": "meskeriu-laikikliai",
    "BITE ALARM / SWINGER": "elektroniniai-kibimo-indikatoriai",
    "BITE ALARM / SWINGER > Svingai": "svingai",
    "FLOATS - PLŪDĖS": "pludes",
    "NETS - GRAIBŠTAI, TINKLELIAI": "graibstai",
    "NETS - GRAIBŠTAI, TINKLELIAI > Kotai": "graibstu-kotai-ir-galvos",
    "SPARE PARTS - ATSARGINĖS DALYS": "atsargines-dalys",
    "Suit - Jacket+Trousers": "apranga-ir-avalyne",
    "Suit - Jacket+Trousers > Striukės": "striukes",
    "Suit - Jacket+Trousers > Kelnės": "ilgos-kelnes",
    "Suit - Kostiumas": "zieminiai-kostiumai",
    "Floatation suit - Neskęstantis kostiumas": "zieminiai-kostiumai",
    "T-shirt - Marškinėliai": "marskineliai",
    "Fleece-Windstopper-Cotton Outfit": "dzemperiai",
    "Trousers - Kelnės": "ilgos-kelnes",
    "Jacket - Striukė": "striukes",
    "Vest - Liemenė": "apranga-ir-avalyne",
    "Underwear - Apatiniai": "termo-apatiniai",
    "Gloves - Pirštinės": "pirstines",
    "Cap - Kepuraitė": "akiniai-ir-kepureles",
    "Hat - Kepurė": "akiniai-ir-kepureles",
    "Sunglasses - Akiniai": "akiniai-ir-kepureles",
    "Shoes - Batai": "batai",
    "Sandals": "avalyne",
    "Wading Shoes - Braidymo batai": "braidymo-batai",
    "Breathable waders": "bridkelnes",
    "Nylon waders": "bridkelnes",
    "Neopren waders": "bridkelnes",
    "Chair / Bedchair - Kėdės / Gultai": "kedes",
    "Chair / Bedchair - Kėdės / Gultai > Gultai": "gultai",
    "Tent / Sleeping Bag - Palapinės, skėčiai, miegmaišiai": "palapines-ir-skeciai",
    "Tent / Sleeping Bag - Palapinės, skėčiai, miegmaišiai > Miegmaišiai": "miegmaisiai",
    "Cookware / Knives - Indai / Peiliai": "indai-ir-irankiai",
    "Tools, Pliers - Žnyplės, žirklės": "indai-ir-irankiai",
    "Tools, Pliers - Žnyplės, žirklės > Žirklutės": "zirklutes",
    "Lamp / Light - Prožektoriai": "prozektoriai-ir-sviestuvai",
    "Accessories - Reikmenys": "kiti-aksesuarai",
    "Weighting Scales / Svarstyklės": "kiti-aksesuarai",
    "Fly fishing": "kiti-aksesuarai",
    "Cannon Accessories": "kiti-prietaisai-ir-priedai",
    "Cannon Accessories > Minn Kota": "valciu-varikliai",
    "Cannon Downriggers": "kiti-prietaisai-ir-priedai",
    "LIVE Rotators": "echolotai",
    "BOSS Audio Electronics": "audio-sistemos",
}
TARGETS: dict[str, str] = {**NEMUS, **STOCK, **FISHING}


class Command(BaseCommand):
    help = "Sumapina Albatros (Stock + nemus.lt) kategorijas ir peržiūri offer'ius."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Tik planas, DB nepaliečiama.")

    def handle(self, *args, **options):
        supplier = Supplier.objects.filter(code="albatros").first()
        if supplier is None:
            raise CommandError("Tiekėjo 'albatros' nėra – pirma importuok Stock failą.")

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

        counts = Counter(
            CategoryMapping.build_source_path(raw, sub)
            for raw, sub in Offer.objects.filter(supplier=supplier).values_list("category_raw", "sub_category_raw")
        )
        for source_path, category in sorted(targets.items(), key=lambda kv: -counts.get(kv[0], 0)):
            self.stdout.write(f"  {counts.get(source_path, 0):>5}  {source_path}  ->  {category.path()}")
        unmapped = {path: n for path, n in counts.items() if path not in targets}
        for path, n in sorted(unmapped.items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"  {n:>5}  {path}  (be eilutės)")
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("--dry-run: DB nepaliesta."))
            return

        created = updated = 0
        with transaction.atomic():
            for source_path, category in targets.items():
                parts = [p.strip() for p in source_path.split(">")]
                _, was_created = CategoryMapping.objects.update_or_create(
                    supplier=supplier,
                    source_path=source_path,
                    defaults={"category": category},
                    create_defaults={
                        "category": category,
                        "source_category_raw": parts[0],
                        "source_sub_category_raw": " > ".join(parts[1:]),
                    },
                )
                created += was_created
                updated += not was_created
        self.stdout.write(f"CategoryMapping: {created} sukurta, {updated} atnaujinta.")

        held = {
            str(code).strip().upper()
            for feed in supplier.feeds.all()
            for code in (feed.config or {}).get("unpublish_codes") or []
        }
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
                and offer.external_id.strip().upper() not in held
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
