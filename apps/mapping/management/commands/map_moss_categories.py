"""MOSS tiekėjo kategorijų mapinimas.

MOSS feed'as atkeliauja su EN kategorijų medžiu ('Predator accessories',
'Fishing lines', ...). Ši komanda idempotentiškai sukuria/atnaujina
CategoryMapping eilutes (raktas: supplier + source_category_raw +
source_sub_category_raw) į geriausiai atitinkančią LT kategoriją ir
permeta esamus offer'ius/produktus (category + is_published).

Paleidimas:  manage.py map_moss_categories [--dry-run]
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.catalog.models import Category
from apps.ingestion.models import Offer
from apps.mapping.models import CategoryMapping
from apps.mapping.services import map_category
from apps.suppliers.models import Supplier

SUPPLIER_CODE = "moss"

# (source_category_raw, source_sub_category_raw) -> LT kategorijos path.
# Path'ai turi sutapti su Category.path() (root > ... > leaf).
MAPPINGS: list[tuple[str, str, str]] = [
    # ---- Meškerės (Rods) ----
    ("Rods", "Rods > Spinning rods", "Meškerykočiai ir priedai > Spiningai"),
    ("Rods", "Rods > Feeder rods", "Meškerykočiai ir priedai > Meškerykočiai dugninei"),
    ("Rods", "Rods > Carp rods", "Meškerykočiai ir priedai > Karpiniai meškerykočiai"),
    ("Rods", "Rods > Telescopic carp rods", "Meškerykočiai ir priedai > Karpiniai meškerykočiai"),
    ("Rods", "Rods > Float fishing", "Meškerykočiai ir priedai > Plūdiniai meškerykočiai"),
    ("Rods", "Rods > Catfish and Sea rods", "Meškerykočiai ir priedai > Meškerykočiai šamams"),
    ("Rods", "Rods > Spod rods", "Meškerykočiai ir priedai > Karpiniai meškerykočiai"),
    ("Rods", "Rods > Fly fishing rods", "Meškerykočiai ir priedai > Muselinės meškerykočiai"),
    ("Rods", "Rods > Ice Fishing Rods", "Poledinė Žūklė > Meškerytės"),
    ("Rods", "Cases and equipment", "Meškerykočiai ir priedai > Meškerių dėklai"),
    # ---- Ritės (Reels) ----
    ("Reels", "Feeder/float fishing > Front drag", "Ritės > Ritės dugninei žvejybai"),
    ("Reels", "Feeder/float fishing > Baitrunner drag", "Ritės > Ritės dugninei žvejybai"),
    ("Reels", "Feeder/float fishing", "Ritės > Ritės dugninei žvejybai"),
    ("Reels", "Carp reels > Baitrunner drag", "Ritės > Karpinės ritės"),
    ("Reels", "Carp reels > Front drag", "Ritės > Karpinės ritės"),
    ("Reels", "Predators > Spinning", "Ritės > Ritės spiningavimui"),
    ("Reels", "Predators > Special reels", "Ritės > Ritės spiningavimui"),
    ("Reels", "Predators > Catfish/Sea fishing", "Ritės > Jūrinės ritės"),
    ("Reels", "Cases and equipment", "Krepšiai, dėklai, dėžutės > Dėklai"),
    # Ritės be sub'ų – daugiausia atsarginės špulės; bendras 'Ritės' šaknis.
    ("Reels", "", "Ritės"),
    # ---- Valai (Fishing lines) ----
    ("Fishing lines", "Monofilaments  - main lines > Carp", "Valai ir pavadėliai > Monofilamentiniai valai"),
    ("Fishing lines", "Monofilaments  - main lines > Spin fishing and feeder", "Valai ir pavadėliai > Monofilamentiniai valai"),
    ("Fishing lines", "Monofilaments  - main lines > Lines for winding machines", "Valai ir pavadėliai > Monofilamentiniai valai"),
    ("Fishing lines", "Monofilaments  - main lines > Catfish", "Valai ir pavadėliai > Jūrai, Šamams Valai"),
    ("Fishing lines", "Braided lines - main lines > Spinning", "Valai ir pavadėliai > Pinti valai"),
    ("Fishing lines", "Braided lines - main lines > Feeder", "Valai ir pavadėliai > Pinti valai"),
    ("Fishing lines", "Braided lines - main lines > Carp & Spod", "Valai ir pavadėliai > Pinti valai"),
    ("Fishing lines", "Braided lines - main lines > Braided lines for winding machines", "Valai ir pavadėliai > Pinti valai"),
    ("Fishing lines", "Braided lines - main lines > Catfish", "Valai ir pavadėliai > Jūrai, Šamams Valai"),
    # Museliniai valai – atskiros kategorijos nėra, bendras 'Valai' šaknis.
    ("Fishing lines", "Braided lines - main lines > Fly lines", "Valai ir pavadėliai"),
    ("Fishing lines", "Braided lines - hook lines > Feeder", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai > Valai pavadėliams"),
    ("Fishing lines", "Braided lines - hook lines > Carp", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai > Valai pavadėliams > Karpiniai pavadėliniai valai"),
    ("Fishing lines", "Braided lines - hook lines > Catfish", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai > Valai pavadėliams"),
    ("Fishing lines", "Rig lines > Rig lines and shock lines", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai > Valai pavadėliams"),
    ("Fishing lines", "Rig lines > Fluorocarbon", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai > Valai pavadėliams > Fluorokarbono pavadėlinis valas"),
    ("Fishing lines", "Accessories", "Kiti aksesuarai"),
    # ---- Predator accessories: masalai, džigai, pavadėliai ----
    ("Predator accessories", "Lures > Soft lures", "Masalai ir jaukai > Spiningui > Guminukai"),
    ("Predator accessories", "Lures > Spoons", "Masalai ir jaukai > Blizgės"),
    ("Predator accessories", "Lures > Spinners", "Masalai ir jaukai > Spiningui > Sukriukės"),
    ("Predator accessories", "Lures > Wobblers", "Masalai ir jaukai > Spiningui > Vobleriai"),
    ("Predator accessories", "Lures > Artificial flies", "Masalai ir jaukai > Spiningui"),
    ("Predator accessories", "Lures > Liquid attractor", "Masalai ir jaukai > Kvapai ir dipai > Skysti kvapai"),
    ("Predator accessories", "Jigs and hooks > No collar", "Masalai ir jaukai > Spiningui > Galvakabliai"),
    ("Predator accessories", "Jigs and hooks > With collar", "Masalai ir jaukai > Spiningui > Galvakabliai"),
    ("Predator accessories", "Jigs and hooks > Spinning hooks", "Kabliukai > Kabliukai spiningavimui"),
    ("Predator accessories", "Leads", "Svareliai ir šeryklėlės > Spiningavimo svareliai"),
    ("Predator accessories", "Catfish", "Svareliai ir šeryklėlės > Svareliai"),
    ("Predator accessories", "Spin fishing systems > Pre-made rigs", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai > Pavadėliai spiningavimui"),
    ("Predator accessories", "Spin fishing systems > Rig accessories", "Suktukai, stabdžiai, gumytės"),
    ("Predator accessories", "Spin fishing accessories", "Kibimo indikatoriai ir plūdės > Plūdės"),
    ("Predator accessories", "Wires > Steel", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai > Metaliniai pavadėliai"),
    ("Predator accessories", "Wires > Tungsten", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai > Pavadėliai spiningavimui"),
    ("Predator accessories", "Wires > Titanium", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai > Metaliniai pavadėliai"),
    ("Predator accessories", "Wires > Fluorocarbon", "Valai ir pavadėliai > Pavadėliai ir pavadėliniai valai > Pavadėliai spiningavimui"),
    # ---- Feeder accessories ----
    ("Feeder accessories", "Feeder accessories > Feeders", "Svareliai ir šeryklėlės > Šėryklėlės"),
    ("Feeder accessories", "Feeder accessories > Rig Components & Terminal tackle", "Suktukai, stabdžiai, gumytės > Feederio aksesuarai"),
    ("Feeder accessories", "Feeder accessories > Hooks", "Kabliukai > Kabliukai dugninei"),
    ("Feeder accessories", "Feeder accessories > Needles and Drills", "Suktukai, stabdžiai, gumytės > Feederio aksesuarai"),
    ("Feeder accessories", "Feeder accessories > Loop tyers and Hook removers", "Suktukai, stabdžiai, gumytės > Feederio aksesuarai"),
    ("Feeder accessories", "Feeder rigs > Barbed - fluorocarbon", "Valai ir pavadėliai > Paruoštos sistemėlės"),
    ("Feeder accessories", "Feeder rigs > Barbed - braided line", "Valai ir pavadėliai > Paruoštos sistemėlės"),
    ("Feeder accessories", "Feeder rigs > Barbless - fluorocarbon", "Valai ir pavadėliai > Paruoštos sistemėlės"),
    ("Feeder accessories", "Feeder rigs > Barbless - braided line", "Valai ir pavadėliai > Paruoštos sistemėlės"),
    ("Feeder accessories", "Feeder rigs > Rig cases", "Krepšiai, dėklai, dėžutės > Dėklai"),
    ("Feeder accessories", "Holders", "Meškerykočiai ir priedai > Stovai, laikikliai, kuoliukai > Meškerių laikikliai"),
    # Feeding – jauko sieteliai/įrankiai.
    ("Feeder accessories", "Feeding", "Masalai ir jaukai > Jaukai ir priedai > Priedai jaukams"),
    ("Feeder accessories", "Baits", "Masalai ir jaukai > Masalai"),
    ("Feeder accessories", "Baits > Wafters", "Masalai ir jaukai > Boiliai ir peletės > Boiliai"),
    ("Feeder accessories", "Baits > Pop up", "Masalai ir jaukai > Boiliai ir peletės > Boiliai"),
    ("Feeder accessories", "Baits > Liquid", "Masalai ir jaukai > Kvapai ir dipai"),
    # Pellet box = pelečių + booster + wafters rinkinys.
    ("Feeder accessories", "Baits > Pellet boxes", "Masalai ir jaukai > Masalai"),
    # ---- Carp accessories ----
    ("Carp accessories", "Carp rigs > Weights and feeders", "Svareliai ir šeryklėlės > Svareliai"),
    ("Carp accessories", "Carp rigs > Accessories for rigs making", "Suktukai, stabdžiai, gumytės > Karpiniai aksesuarai"),
    ("Carp accessories", "Carp rigs > Complete rigs", "Valai ir pavadėliai > Paruoštos sistemėlės"),
    ("Carp accessories", "Carp rigs > Hooks", "Kabliukai > Karpiniai kabliukai"),
    ("Carp accessories", "Carp rigs > Needles and Drills, Loop tyers and Hook removers", "Suktukai, stabdžiai, gumytės > Karpiniai aksesuarai"),
    ("Carp accessories", "Carp rigs > Ronnie Rig", "Valai ir pavadėliai > Paruoštos sistemėlės"),
    ("Carp accessories", "PVA products", "Masalai ir jaukai > PVA"),
    # Feeding – spod'ai, kobros, mėtimo lazdos, kaušeliai.
    ("Carp accessories", "Feeding", "Masalai ir jaukai > Laidynės, kobros, raketos"),
    ("Carp accessories", "Carp baits > Boilies", "Masalai ir jaukai > Boiliai ir peletės > Boiliai"),
    ("Carp accessories", "Carp baits > Dipped boilies", "Masalai ir jaukai > Boiliai ir peletės > Boiliai"),
    ("Carp accessories", "Carp baits > Wafters", "Masalai ir jaukai > Boiliai ir peletės > Boiliai"),
    ("Carp accessories", "Carp baits > Artificial baits", "Masalai ir jaukai > Boiliai ir peletės > Dirbtiniai karpiniai masalai"),
    ("Carp accessories", "Carp baits > Liquid", "Masalai ir jaukai > Kvapai ir dipai"),
    # ---- Universal accessories ----
    ("Universal fishing accessories", "Universal accessories > Swivels and snaps", "Suktukai, stabdžiai, gumytės > Suktukai"),
    ("Universal fishing accessories", "Universal accessories > Stoppers and side rigs", "Suktukai, stabdžiai, gumytės"),
    ("Universal fishing accessories", "Single hooks > Feeder", "Kabliukai > Kabliukai dugninei"),
    ("Universal fishing accessories", "Single hooks > Carp", "Kabliukai > Karpiniai kabliukai"),
    ("Universal fishing accessories", "Single hooks > Catfish", "Kabliukai > Kabliukai šamams"),
    ("Universal fishing accessories", "Double hooks and triple hooks", "Kabliukai > Kabliukai spiningavimui"),
    ("Universal fishing accessories", "Weights and feeders > Shots", "Svareliai ir šeryklėlės > Svareliai"),
    ("Universal fishing accessories", "Weights and feeders > Weights", "Svareliai ir šeryklėlės > Svareliai"),
    ("Universal fishing accessories", "Float fishing > Fishing floats", "Kibimo indikatoriai ir plūdės > Plūdės"),
    ("Universal fishing accessories", "Float fishing > Complete rigs", "Valai ir pavadėliai > Paruoštos sistemėlės"),
    # ---- Kibimo indikatoriai ----
    ("Bite alarms and indicators", "Bite indicators", "Kibimo indikatoriai ir plūdės > Mechaniniai kibimo indikatoriai"),
    ("Bite alarms and indicators", "Bite alarms > Alarm sets", "Kibimo indikatoriai ir plūdės > Elektroniniai kibimo indikatoriai"),
    ("Bite alarms and indicators", "Bite alarms > Individual alarms", "Kibimo indikatoriai ir plūdės > Elektroniniai kibimo indikatoriai"),
    ("Bite alarms and indicators", "Accessories", "Kibimo indikatoriai ir plūdės > Elektroniniai kibimo indikatoriai"),
    ("Bite alarms and indicators", "Snag Gears", "Kibimo indikatoriai ir plūdės > Elektroniniai kibimo indikatoriai"),
    # ---- Apranga ir avalynė ----
    ("Boots, clothes, sunglasses", "Boots and high boots", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Avalynė > Auliniai batai"),
    ("Boots, clothes, sunglasses", "T-shirts and tanks tops", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Marškinėliai"),
    ("Boots, clothes, sunglasses", "T-shirt UV", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Marškinėliai"),
    ("Boots, clothes, sunglasses", "Outerwear > Insulated", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Striukės"),
    ("Boots, clothes, sunglasses", "Outerwear > Non-insulated", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Striukės"),
    ("Boots, clothes, sunglasses", "Hoodies", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Džemperiai"),
    ("Boots, clothes, sunglasses", "Thermals", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Termo apatiniai"),
    ("Boots, clothes, sunglasses", "Waders and chestwaders", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Kelnės ir šortai > Bridkelnės"),
    ("Boots, clothes, sunglasses", "Joggers and shorts", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Kelnės ir šortai"),
    ("Boots, clothes, sunglasses", "Gloves", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Pirštinės"),
    ("Boots, clothes, sunglasses", "Sunglasses", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Akiniai ir kepurėlės"),
    ("Boots, clothes, sunglasses", "Beanies > Caps and hats", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Akiniai ir kepurėlės"),
    ("Boots, clothes, sunglasses", "Beanies > Winter beanie", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė > Akiniai ir kepurėlės"),
    ("Boots, clothes, sunglasses", "Socks", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė"),
    ("Boots, clothes, sunglasses", "Scarfs", "Drabužiai, Palapinės, Baldai, Įrankiai > Apranga ir avalynė"),
    ("Boots, clothes, sunglasses", "Wallets and belts", "Kiti aksesuarai"),
    ("Boots, clothes, sunglasses", "Towels, cloth accessories", "Kiti aksesuarai"),
    # ---- Graibštai ----
    ("Landing nets", "Handles and heads > Feeder and match heads", "Graibštai ir priedai > Graibštų kotai ir galvos"),
    ("Landing nets", "Handles and heads > Handles", "Graibštai ir priedai > Graibštų kotai ir galvos"),
    ("Landing nets", "Handles and heads > Accessories", "Graibštai ir priedai > Graibštų priedai"),
    ("Landing nets", "Landing nets > Folded", "Graibštai ir priedai > Graibštai"),
    ("Landing nets", "Landing nets > Carpfishing", "Graibštai ir priedai > Graibštai"),
    ("Landing nets", "Landing nets > for Spinning", "Graibštai ir priedai > Graibštai"),
    ("Landing nets", "Landing nets > Spare landing net meshes", "Graibštai ir priedai > Graibštų priedai"),
    ("Landing nets", "Keep nets", "Graibštai ir priedai > Tinkleliai žuvims laikyti"),
    ("Landing nets", "Drop nets", "Graibštai ir priedai > Graibštai"),
    # ---- Krepšiai / baldai / palapinės ----
    ("Bags and Camping", "Different bag types > EVA bags", "Krepšiai, dėklai, dėžutės > Krepšiai"),
    ("Bags and Camping", "Different bag types > CarryALL", "Krepšiai, dėklai, dėžutės > Krepšiai"),
    ("Bags and Camping", "Different bag types > Food and thermal bags", "Krepšiai, dėklai, dėžutės > Krepšiai"),
    ("Bags and Camping", "Different bag types > Backpacks", "Krepšiai, dėklai, dėžutės > Kuprinės"),
    ("Bags and Camping", "Bags by fishing type > Carp C2G", "Krepšiai, dėklai, dėžutės > Krepšiai"),
    ("Bags and Camping", "Bags by fishing type > Feeder", "Krepšiai, dėklai, dėžutės > Krepšiai"),
    ("Bags and Camping", "Bags by fishing type > Spinning", "Krepšiai, dėklai, dėžutės > Krepšiai"),
    ("Bags and Camping", "Bags by fishing type > Carp Carpath", "Krepšiai, dėklai, dėžutės > Krepšiai"),
    ("Bags and Camping", "Bags by fishing type > Carp Classa and Queen", "Krepšiai, dėklai, dėžutės > Krepšiai"),
    ("Bags and Camping", "Bags by fishing type > Specials", "Krepšiai, dėklai, dėžutės > Krepšiai"),
    ("Bags and Camping", "Cases > Rod cases", "Meškerykočiai ir priedai > Meškerių dėklai"),
    ("Bags and Camping", "Cases > Reel cases", "Krepšiai, dėklai, dėžutės > Dėklai"),
    ("Bags and Camping", "Cases > Landing net and net cases", "Krepšiai, dėklai, dėžutės > Dėklai"),
    ("Bags and Camping", "Bags, mats and weighing > Keepnets and weigh slings", "Krepšiai, dėklai, dėžutės > Matai ir krepšiai žuvims"),
    ("Bags and Camping", "Bags, mats and weighing > Mats and cradles", "Krepšiai, dėklai, dėžutės > Matai ir krepšiai žuvims"),
    ("Bags and Camping", "Chairs and tables > Chairs", "Drabužiai, Palapinės, Baldai, Įrankiai > Baldai Žvejams > Kėdės"),
    ("Bags and Camping", "Chairs and tables > Tables", "Drabužiai, Palapinės, Baldai, Įrankiai > Baldai Žvejams > Staliukai ir spintelės"),
    ("Bags and Camping", "Beds and sleeping bags > Beds", "Drabužiai, Palapinės, Baldai, Įrankiai > Baldai Žvejams > Gultai"),
    ("Bags and Camping", "Beds and sleeping bags > Sleeping bags", "Drabužiai, Palapinės, Baldai, Įrankiai > Baldai Žvejams > Miegmaišiai"),
    ("Bags and Camping", "Umbrellas and bivouacs > Umbrellas and accessories", "Drabužiai, Palapinės, Baldai, Įrankiai > Palapinės ir skėčiai"),
    ("Bags and Camping", "Umbrellas and bivouacs > Bivouacs and accessories", "Drabužiai, Palapinės, Baldai, Įrankiai > Palapinės ir skėčiai"),
    ("Bags and Camping", "Kitchenware and utensils > Cups, bottles, thermos flasks, cooling boxes", "Indai"),
    ("Bags and Camping", "Kitchenware and utensils > Food and drinks", "Indai"),
    ("Bags and Camping", "Portable toilet", "Kiti aksesuarai"),
    # ---- Stovai / laikikliai ----
    ("Rodpods and Rod rests", "Rodpods", "Meškerykočiai ir priedai > Stovai"),
    ("Rodpods and Rod rests", "Rodpods > Accessories", "Meškerykočiai ir priedai > Stovai"),
    ("Rodpods and Rod rests", "Buzz bars and rod holders > Feeder", "Meškerykočiai ir priedai > Stovai, laikikliai, kuoliukai > Meškerių laikikliai"),
    ("Rodpods and Rod rests", "Buzz bars and rod holders > Versatile", "Meškerykočiai ir priedai > Stovai, laikikliai, kuoliukai > Meškerių laikikliai"),
    ("Rodpods and Rod rests", "Buzz bars and rod holders > Carp", "Meškerykočiai ir priedai > Stovai, laikikliai, kuoliukai > Meškerių laikikliai"),
    ("Rodpods and Rod rests", "Banksticks", "Meškerykočiai ir priedai > Stovai, laikikliai, kuoliukai > Stovai ir kuoliukai"),
    ("Rodpods and Rod rests", "Catfish rod holder", "Meškerykočiai ir priedai > Stovai, laikikliai, kuoliukai > Meškerių laikikliai"),
    # ---- Įrankiai / įranga ----
    ("Angling equipment", "Tackle boxes > Special", "Krepšiai, dėklai, dėžutės > Dėžutės"),
    ("Angling equipment", "Tackle boxes > Simple", "Krepšiai, dėklai, dėžutės > Dėžutės"),
    ("Angling equipment", "Tackle boxes > Bunk", "Krepšiai, dėklai, dėžutės > Dėžutės"),
    ("Angling equipment", "Tackle boxes > Cases", "Krepšiai, dėklai, dėžutės > Dėklai"),
    ("Angling equipment", "Scissors and pliers", "Drabužiai, Palapinės, Baldai, Įrankiai > Indai ir įrankiai"),
    ("Angling equipment", "Knives and grinders", "Drabužiai, Palapinės, Baldai, Įrankiai > Indai ir įrankiai"),
    ("Angling equipment", "Scales and Tapes", "Drabužiai, Palapinės, Baldai, Įrankiai > Indai ir įrankiai"),
    ("Angling equipment", "Forceps > Bent", "Drabužiai, Palapinės, Baldai, Įrankiai > Indai ir įrankiai"),
    ("Angling equipment", "Forceps > Straight", "Drabužiai, Palapinės, Baldai, Įrankiai > Indai ir įrankiai"),
    ("Angling equipment", "Aiguilles, Needles, Loop tyers and Hook removers", "Drabužiai, Palapinės, Baldai, Įrankiai > Indai ir įrankiai"),
    ("Angling equipment", "Buckets", "Krepšiai, dėklai, dėžutės > Kibirai ir kanistrai"),
    ("Angling equipment", "Trolleys", "Drabužiai, Palapinės, Baldai, Įrankiai > Karučiai"),
    ("Angling equipment", "Catapults and throwing sticks", "Masalai ir jaukai > Laidynės, kobros, raketos"),
    # Markeriniai plūdės / buoys.
    ("Angling equipment", "Markers and buoys", "Kibimo indikatoriai ir plūdės > Plūdės"),
    ("Angling equipment", "For keeping catches", "Kiti aksesuarai"),
    ("Angling equipment", "Chemical lights", "Kiti aksesuarai"),
    ("Angling equipment", "Boat accessories", "Kiti aksesuarai"),
    ("Angling equipment", "Yoyos & magnets", "Kiti aksesuarai"),
    ("Angling equipment", "Lights > Lights", "Elektronika > Prožektoriai ir šviestuvai"),
    ("Angling equipment", "Lights > Head lamps", "Elektronika > Prožektoriai ir šviestuvai"),
    ("Angling equipment", "Lights > Motion sensors", "Elektronika > Kiti prietaisai ir priedai"),
    ("Angling equipment", "Lights > Power banks", "Elektronika > Kiti prietaisai ir priedai"),
    # ---- Papildomas asortimentas / dovanos ----
    ("Additional assortment", "Presents > Fishing stickers", "Dovanos"),
    ("Additional assortment", "Presents > Key chains, patches, belts, wallets", "Dovanos"),
    ("Additional assortment", "Presents > Fishing mat", "Dovanos"),
    ("Additional assortment", "Presents > Bags", "Dovanos"),
    ("Additional assortment", "Hand watch", "Dovanos"),
    ("Additional assortment", "Car accessories", "Kiti aksesuarai"),
    # 'Other' – šiuo metu 'Set of feeder rigs' (paruoštos sistemėlės).
    ("Other", "", "Valai ir pavadėliai > Paruoštos sistemėlės"),
]

# Sąmoningai NEmap'inami (NULL geriau nei klaidinga kategorija):
#  - 'Store equipment'/'Store equipment -%' – parduotuvės įranga/demo,
#    mišrus turinys (meškerykočiai, ritės, stendai).
#  - 'Additional assortment > For shops' – prekybos vietų įranga.
#  - ''/'' – be kategorijos.
LEAVE_UNMAPPED = {
    ("Store equipment", ""),
    ("Store equipment -%", ""),
    ("Additional assortment", "For shops"),
    ("", ""),
}


def _category_by_path(path: str) -> Category | None:
    """Randa kategoriją pagal pilną path ('Root > ... > Leaf').

    Dubliukų atveju (identiški path'ai, pvz. 'Muselinės meškerykočiai')
    imamas mažiausias pk – originali kategorija.
    """
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
        "MOSS CategoryMapping eilutės (EN -> LT kategorijų medis) + "
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

        # Path -> Category rezoliucija
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

        # Report: source -> target + offer kiekis
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
            combo: n
            for combo, n in offer_counts.items()
            if combo not in targets
        }
        self.stdout.write("\nSąmoningai nemap'inama / be eilutės:")
        for (source_cat, source_sub), count in sorted(
            unmapped.items(), key=lambda kv: -kv[1]
        ):
            flag = " (LEAVE_UNMAPPED)" if (source_cat, source_sub) in LEAVE_UNMAPPED else ""
            self.stdout.write(
                f"  {count:>5}  {source_cat} | {source_sub}{flag}"
            )
        self.stdout.write(
            f"\nCoverage: {mapped_offers}/{total_offers} "
            f"= {mapped_offers / total_offers * 100:.1f}% offer'ių"
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: DB nepaliesta."))
            return

        # 1) CategoryMapping upsert'ai
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
        self.stdout.write(
            f"CategoryMapping: {created} sukurta, {updated} atnaujinta."
        )

        # 2) Esamų offer'ių remap'inimas (category + is_published + product)
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
            # Pipeline publish taisyklė: kategorija + kaina + nuotraukos
            # (publish_without_images moss feed'ams neleidžiamas).
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
