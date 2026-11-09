# Albatros integracija (planas agentams)

> Statusas (2026-11-05): **infrastruktūra paruošta, Albatros adapterio dar nėra.**
> Šis failas – nurodymai lokaliems ir Devin agentams. Bendra būsena ir taisyklės – `AGENTS.md`.
>
> | Dalis | Būsena |
> |---|---|
> | Planas, sprendimai, kainodara | ✅ šis failas |
> | Per-sandėlio kaina / savikaina / nuolaida % (normalyzer → BE) | ✅ normalyzer #35, BE #13 (prod) |
> | Prekių badge'ai (admin, taisyklės, importas, API, Typesense, FE) | ✅ BE #14/#15, FE #5 (prod) |
> | Normalyzer eksportas siunčia `badges` | ✅ `NormalizedItem.badges` → `Offer.badges` → katalogo `badges` |
> | Adapteris `albatros` (Stock Excel) | ✅ `apps/suppliers/adapters/albatros.py`, variantai grupuojami pagal gamintojo puslapį |
> | Gamintojų scraper'iai | 🟡 Savage Gear, DAM, Prologic, Madcat (Shopify + sportfishtackle.de); Mepps ❌ |
> | PS prekių re-key, kategorijų mapingas, vertimas, eksportas | ❌ |
> | Prod importas | ❌ **tik gavus vartotojo patvirtinimą**, po dry-run ataskaitos |

## Pagrindiniai faktai

- Tiekėjas `albatros`, shop SKU prefiksas `AL.` (`AL.<kodas>`).
- Albatros – tame pačiame mieste, **vietinis sandėlis**. BE warehouse `albatros`,
  fronte pavadinimas **„Centrinis sandėlis“**.
- Jokio API/feed'o: visi duomenys ateina **Excel failais**, kuriuos įkelia vartotojas.
  Likučių ir kainų sinchronizacija vyksta **tik įkėlus failą** (jokio tvarkaraščio,
  jokių užklausų tiekėjui).
- Excel'yje tik: kodas, pavadinimas EN, savikaina be PVM, SALE %, likutis, kartais FOTO URL. Kategorijų nėra –
  tik **skyriaus** antraštė (eilutė, kur užpildytas tik A stulpelis, pvz. `Bait / RAPALA`,
  `Reel - Ritė`). Skyrius = orientacija į stambesnę kategoriją.
- Turinys (aprašymai, nuotraukos, specifikacijos, variantų grupavimas) – **scrap'inamas
  iš gamintojų svetainių pagal kodą**.

## Failų tipai

| Tipas | Pavyzdys | Stulpeliai | Rolė |
|---|---|---|---|
| **Stock (pagrindinis, pilnas)** | `Stock 10.21.xlsx`: 10 398 kodų (visi su likučiu ≥ 1; 6 588 be SALE), 110 skyrių | A žymė (`""` / `New 2027`), Code, Item, `be PVM, export`, `SALE`, `Q-ty`, Order, Catalogue, `FOTO` | **Katalogas + savikaina + likučiai.** Importuojama **viskas**, įskaitant 0. Kito Stock importo metu nerastas kodas → likutis 0 |
| Raw Stock | `Raw Stock 10.26.xlsx` (~30 800 kodų) | A skyrius, kodas, pavadinimas, kaina be PVM, likutis | **Nenaudojamas** kaip šaltinis (tik informacija analizei) |
| Naujienos | `2027 NEW.xlsx` (382) | Code, Item, su PVM, export, nuolaida, FOTO (salsify) | Naujienų žymė + nuotraukos; **nenulina** |
| Gamintojo kainoraščiai | `TiCA 2024`, `Crazy Fish Softlures 2024`, `Outdoors Adventures 2024` (Humminbird/Jobe/RAM: EAN, RRP, foto) | skiriasi | Papildomi duomenys (EAN, RRP, foto); **nenulina** |

Stock failo taisyklės:
- Skyrius = eilutė, kur užpildytas tik A stulpelis (pvz. `Reel - Ritė`) → `category_raw`.
- A stulpelis `New 2027` (400 eilučių) = **naujienos žymė** (žr. „Naujienų badge“).
- `SALE` – neigiama trupmena (`-0.3` = −30 %), užpildyta 3 810 eilučių; tuščia = be nuolaidos.
- `FOTO` URL: ~4 800 `images.salsify.com` (DAM grupė), albatros.lt, jobesports.com.
- Kodą normalizuoti: `str(x).strip()`, skaičius be `.0`, brūkšnius išlaikyti (`10005-100`).
  Dubliuotas kodas – paskutinė eilutė + įspėjimas.
- Failo tipą atpažinti pagal antraštes (`Code`, `be PVM, export`, `SALE`, `Q-ty`), ne pagal vardą.
  Neatpažintas → klaida, nieko nekeisti.
- Lietuviški bendriniai pirmi žodžiai (`VOBLERIS`, `BALANSYRAS`, `SUKRIUKĖ`, `VARIKLIS`,
  `BLIZGĖ`, `GUMINUKAS`) nėra ženklas: ženklas = kitas žodis arba iš skyriaus
  (`VOBLERIS RAPALA BXB-3 BGH` → Rapala). Santrumpos: `SG` = Savage Gear, **`PL` = Prologic**,
  `R.T.`/`RT` = Ron Thompson, `CC` = CC Moore, `FZ` = Effzett.

## Apimtis ir likučiai (vartotojo sprendimas)

- Importuojama **viskas, kas yra Stock faile**, įskaitant likutį 0.
- Kitame Stock importe nerastas kodas → likutis 0 (prekė netrinama). Saugiklis: tik jei importe
  0 klaidų ir failas atpažintas kaip pilnas (`mark_absent_offers`, `ABSENT_MIN_SEEN_SHARE`).
- Senos PS prekės, kurių kodas yra Albatros faile = **Albatros prekės**: jų likutis imamas iš
  Stock failo, įskaitant nulinimą.

## Kainodara (vartotojo sprendimas)

`SALE` yra **mūsų papildoma nuolaida** (neigiama trupmena, `-0.3` = −30 %).

```
savikaina_be_nuolaidos = export                      # `be PVM, export`
savikaina_su_nuolaida  = export × (1 + SALE)        # įrašoma kaip cost (BE cost_eur)
pardavimo_kaina        = export × 1,5                # įprasta kaina (price_net)
nuolaida_%             = round((1 − (savikaina_su_nuolaida×1,5) / pardavimo_kaina) × 100)
                       = round(−SALE × 100)          # sveikas %, BE InventoryItem.offer_discount_percent
galutinė_kaina         ≈ savikaina_su_nuolaida × 1,5
```

Pavyzdys: export 10 €, SALE −0.3 → cost 7 €, kaina 15 €, nuolaida 30 %, galutinė 10,50 € (= 7 × 1,5).

- Be `SALE` → cost = export, nuolaidos nėra (`offer_discount_percent = null`).
- `SALE` dingo kitame faile → nuolaida nuimama.
- **Padaryta:** adapteris turi užpildyti `NormalizedItem.discount_percent` (= `round(−SALE×100)`,
  be SALE – `None`); jis saugomas `Offer.discount_percent`, įeina į content hash ir per
  `offer_terms()` keliauja į BE `offers.<warehouse>.discount_percent` → `InventoryItem.offer_discount_percent`.
  `price_net` lieka be nuolaidos (export × 1,5), `cost_net` = su nuolaida. BE pats skaičiuoja
  `compare_at_price` / `discount_percent` fronte.

## Susiejimas su esamomis BE prekėmis

Albatros kodai = gamintojo art. nr. Analizės metu BE rastas 3 281 aktyvus variantas su
Albatros kodu (sku arba sku be prefikso):

| Šaltinis BE | Ženklai (variantų) | Veiksmas |
|---|---|---|
| **PrestaShop** (`import_source=prestashop`) | Savage Gear 367, Jobe 286, Prologic 151, DAM 147, Fox 74, Crazy Fish 66, Okuma 30, Madcat 25, Mepps 20, Imax 15, Minn Kota 15, Rapala 11 … | Tai **Albatros prekės**. Perkelti ant normalyzer'io (re-key kaip RM/FF, žr. `prestashop_one_time_import.md`): URL ir istorija išlieka, SKU → `AL.<kodas>`, likutis iš Excel |
| **Normalyzer, kiti tiekėjai** (fox `FX.`, shimano `SH.`, korda `KD.`, ockert `OC.`) | Fox 685, Shimano 502, Fox Rage 441, Matrix 249, CC Moore 63, Guru 44, PowerPro 17, Spomb 16, Salmo 5 | **Tik papildomas sandėlis** `albatros` prie esamo varianto. Turinio, kategorijos, pavadinimo neliesti |
| Nėra BE | ~27 000 kodų (su likučiu ~8 000) | Nauja prekė per gamintojo turinį |

- Prieš bet kokį re-key: **BE DB backup** (`pg_dump -Fc`), dry-run ataskaita vartotojui.
- Neaiškūs atitikmenys (kodas sutampa, bet ženklas/pavadinimas ne) → CSV peržiūrai, ne automatiškai.
- Nesusietos senos PS prekės lieka **įjungtos**.
- **Ta pati prekė pas kelis tiekėjus** (vartotojo sprendimas): įrašomos **abi kainos** (kiekvieno
  sandėlio), fronte rodoma **mažesnė**, o pristatymo laikas – pagal to sandėlio
  `Warehouse.dispatch_days_min/max` (Albatros – vietinis, beveik kaip parduotuvė).
  - **Padaryta (normalyzer #35, BE #13):**
    - Pirminis offer'is (prekės „savininkas“) nustato varianto kainą ir siunčia sandėliui
      `cost_net` + `discount_percent`.
    - Papildomo sandėlio offer'is: `Offer.shop_variant_code` = kito tiekėjo shop SKU (pvz. `FX.123`).
      Toks offer'is **nekuria turinio**, į `plan_prices()` neįeina (neperrašo varianto kainos);
      siunčia tik likutį ir sandėlio galutinę kainą `price_net` (nuolaida jau įskaičiuota) →
      `InventoryItem.offer_price_override_eur`.
    - `price_locked` offer'is sandėlio sąlygų nesiunčia (`offer_terms()` → `None`).
    - BE renkasi sandėlį su `qty_available > 0` pagal `offer_priority`, efektyvią kainą, id;
      pristatymo langas – pagal parinktą sandėlį. FE pakeitimų nereikėjo.
  - Albatros adapteriui lieka: Albatros kodą, sutampantį su kito tiekėjo variantu, pažymėti
    `shop_variant_code` (susiejimas pagal kodą/EAN, neaiškūs – CSV peržiūrai).
  - Po pirmo deploy'aus BE vieną kartą įrašo savikainas visiems sandėliams (didesnis stock sync).

## Naujienų badge („New 2027“)

Šaltinis: Stock failo A stulpelis `New 2027` ir (arba) kodas yra `2027 NEW.xlsx`.

**Sprendimas (vartotojo):** atskiri marketingo badge'ai, **ne** feature (feature = techniniai
parametrai). BE jau turi (ecommercebackend `docs/badges.md`):
- `Badge` (kuriamas admine bet kada: kodas, LT pavadinimas, stilius/spalva, prioritetas, galioja nuo–iki),
- `ProductBadge` su šaltiniu `manual` / `import` / `rule`,
- `BadgeRule` (taisyklės kaip enrichment: ženklas, kategorija, SKU prefiksas, feature, tekstas,
  likutis sandėlyje, nuolaida, naujumas) – naktį 03:50 arba admine.
- Katalogo importe prekės lauke `badges: ["new-2027"]` → BE sinchronizuoja **tik `import`**
  šaltinio badge'us: trūkstamą kodą sukuria (`name=code`, pervadinti admine), nebeatsiųstą nuima.
  `badges` nenurodytas (`null`) = nieko nekeisti; `[]` = nuimti visus importo badge'us.

**Normalyzer (padaryta):** adapteris pildo `NormalizedItem.badges` (Albatros: `["new-2027"]`,
kai A stulpelyje `New 2027` arba kodas yra `2027 NEW.xlsx`, kitaip `[]`). Saugoma `Offer.badges`,
įeina į content hash ir `exported_hash`; katalogo payload'e prekės `badges` = publikuotų offer'ių
sąjunga. Adapteriai, kurie lauko nepildo (`None`), `badges` rakto nesiunčia – shop'e jų badge'ai
nekeičiami. Žymė dingo iš **validaus** Stock failo → `[]` → BE nuima.
Ribojimas: offer'is su `shop_variant_code` (prekė kito tiekėjo) badge'o shop'ui neperduoda –
tokioms prekėms „Naujiena“ reikia BE `BadgeRule` arba rankinio priskyrimo.

## Gamintojų šaltiniai (vartotojo pateikti)

### Įgyvendinta (`scrape_manufacturer --source <ženklo slug>`)

`source` = prekės ženklo slug (`slugify(brand_name_raw)`: `savage-gear`, `dam`, `prologic`,
`madcat`, `mepps`), nes `translate_lang` turinio ieško pagal jį ir kodą (pirma EN, tada DE).
Vienas `source` jungia kelis puslapių šaltinius (`apps/suppliers/manufacturers/brands.py`):

| Modulis | Svetainė | Kalba | Kas gaunama |
|---|---|---|---|
| `shopify.py` | savagegear.com, dam.de, prologicfishing.com, madcat-fishing.com (`/products.json`) | en | prekė = bazinė prekė; `variant.sku` = kodas (`SVS65045` → `65045`); option'ai (Color, Size…) = varianto eilutė; nuotraukos; aprašymas; `product_type` |
| `sportfishtackle.py` | sportfishtackle.de/de/artiklar/<slug>.html (sąrašas iš sportfiskeprylar.se sitemap, slug prasideda ženklu) | de | JSON-LD `offers[].sku` = kodas, `gtin12` = EAN (`specs.eans`), varianto pavadinimas; `TeknSpec` lentelė → sekcijos tekstas; zoom nuotraukos |
| `http.py` | – | – | User-Agent, pauzė ≥ 1 s, 429/5xx → backoff (Retry-After), klaida → `None` (crawl tęsiasi) |

Faktai (2026-10, Stock 10.21):
- **Gamintojų Shopify turi tik dabartinį sezoną** (savagegear.com – 108 prekės): Stock kodų
  rasta Savage Gear 12/3 494, DAM 83/943, Prologic 156/554, Madcat 192/430.
- **sportfiskeprylar/sportfishtackle turi ir senus sezonus:** Savage Gear 1 369/3 494 kodų
  (212 puslapių). Kodai sutampa su Albatros (5 ir 7 skaitmenų).
- sportfishtackle.**com** (EN) blokuoja Cloudflare (403) – neapeiti; naudojama `.de`.
- sportfiskeprylar.se `/en/` puslapiai vis tiek grąžina švedišką tekstą.
- **store.mepps.com** – JAV parduotuvė (nopCommerce), SKU kitokie (`B0FT BRT-BRG`), su Albatros
  kodais (`528981`, `528987-B`) nesutampa. sportfishtackle turi tik 3 Mepps puslapius.
  Mepps šaltinis neišspręstas.
- Prekės be gamintojo turinio: naudoti Stock failo salsify nuotraukas ir pavadinimą; kitų
  mažmenininkų/šaltinių paieška – kitas žingsnis.

### Pradinė analizė

Patikrinta iš Devin VM (paieška pagal Albatros kodą):

| Ženklas | Kodų / su likučiu | Šaltinis | Paieška pagal kodą |
|---|---|---|---|
| Savage Gear (`SAVAGE`, `SG`) | 6 124 / 3 452 | savagegear.com (Shopify); sportfiskeprylar.se | ✅ `/search?q=<kodas>&type=product` randa |
| DAM | 2 320 / 911 | dam.de | ✅ `/search?q=<kodas>` randa |
| Mepps | 1 424 / 556 | store.mepps.com; sportfiskeprylar.se | ✅ `/search?q=<kodas>` randa |
| Prologic (`PROLOGIC`, `PL`) | 1 170 / 554 | prologicfishing.com; sportfiskeprylar.se | ⚠ HTTP 429 (rate limit) – lėtai, iš srv2 |
| Madcat | 594 / 430 | madcat-fishing.com | ⚠ HTTP 429 |
| Kuusamo | 786 / 248 | kuusamonuistin.fi/en | ⚠ HTTP 429 |
| Crazy Fish | 2 639 / 1 321 | crazy-fish.eu (+ crazyfish.com.ua RU/UA, crazyfishetcompagnie.fr) | ❌ kodas (`ALSR662XULS`) paieškoje nerandamas – katalogo crawl + modelio atitikimas |
| Rapala (`VOBLERIS RAPALA`) | ~2 500 / ~330 | rapala.eu; sportfiskeprylar.se | ❌ Albatros kodas `BXB03BGH` ≠ svetainės formatas (`BXB-3 BGH`) – atitikimas per modelis+dydis+spalva |
| Lucky John | 1 047 / 375 | salmo.lv/brand/lucky-john (LV/RU) | neištirta |
| Lucky Craft | – | luckycraft.fr | ❌ paieška 404 – crawl |
| Jobe | 916 / 357 | jobesports.com; foto `uploads/product/<kodas>-zoom.jpg` Excel'yje | foto jau yra |
| Minn Kota / Humminbird | ~800 / ~570 | johnsonoutdoors (foto Stock faile) | neištirta |
| TICA | 130 / 63 | ticafishing.com (jau naudojamas Ockert) | – |

- DAM grupės (Savage Gear, DAM, Prologic, Madcat, Imax, Scierra, Ron Thompson) nuotraukos jau
  yra Stock failuose: ~4 800 `images.salsify.com` URL. Naudoti, kai gamintojo puslapis nerastas.
- Kiekvienas gamintojas – atskiras modulis `apps/suppliers/manufacturers/<source>.py`
  (kaip `sportex.py`, `climax.py`) → `ManufacturerContent`
  (`source`, `url`, `language`, `series`, `description`, `specs`, `codes`, `images`).
  Paleidimas per `scrape_manufacturer` (pridėti į `SOURCES`).
- Mandagiai: `delay` ≥ 1 s, User-Agent, kešas (`ManufacturerContent` unikalus pagal `source+url`),
  429 → exponential backoff. Ilgi crawl'ai – one-off konteineryje srv2 (`--network coolify`).
- Gamintojo puslapis = **bazinė prekė**, jo variantų lentelė/selektorius (kodai) = **variantai**.
  `product_group` = gamintojo puslapio slug. Kodas be puslapio → atskira prekė; be nuotraukos
  nepublikuojama.
- Tekstai neutralūs (ta pati `translate_lang` schema: be „mūsų“, kitų serijų, reklamos).

## Eiga

1. **Adapteris `albatros`** (`apps/suppliers/adapters/albatros.py`, `@register_adapter`):
   Stock failas (FeedRole `catalog`, upload); skyrius → `category_raw`;
   kodas → `external_id`, `sku=AL.<kodas>`; `export × (1+SALE)` → pirkimo kaina (cost),
   `export × 1,5` → kaina; SALE → `discount_percent`;
   `New 2027` → badge; likutis → warehouse `albatros`.
   Papildomi failai (Stock su FOTO, NEW, kainoraščiai) – atskiri feed'ai (`partial_update`),
   papildo nuotraukas/EAN/RRP, **nenulina**.
2. **Apimtis:** visi Stock failo kodai (ir su 0).
3. **Gamintojo turinys:** `scrape_manufacturer --source <x>`; susiejimas pagal kodą
   (`ManufacturerContent.codes`), grupavimas į bazinę prekę + variantus.
4. **Esamų prekių susiejimas:** PS → re-key, kiti tiekėjai → tik sandėlis.
5. **Kategorijų mapingas** `map_albatros_categories.py`: 116 skyrių → BE kategorijos,
   nesumapinta → `Nepriskirta`. Per stambius skyrius (`Bait / Wobbler`) tikslinti pagal
   gamintojo kategoriją.
6. **Vertimas į LT** (`translate_lang`, ta pati schema).
7. **Eksportas:** `export_catalog --supplier albatros --dry-run` → ataskaita → `--push`;
   BE ImportRun be klaidų; **Typesense** reindex (`search.services.index_products`) ir patikra.
8. **Likučiai:** naujas Stock failas → importas → `export_stock`.
   `mark_absent_offers` nulina dingusius tik jei importe 0 klaidų ir matyta ≥ `ABSENT_MIN_SEEN_SHARE`.

## Saugikliai (nekeisti)

- Klaida faile ≠ likutis 0 (`mark_absent_offers`).
- `fetch_errors` logika `apps/ingestion/pipeline.py`.
- `_product_targets`, `_write_product_text` (`translate_lang.py`), `_propagate_product_text` (`pipeline.py`).
- `sorted(all_axes)` ir `_shared_eans` (`apps/catalog_export/services.py`).
- Nemerginti be vartotojo patvirtinimo. Prieš BE pakeitimus – backup.

## Testai (privalomi prieš PR)

- Visų failų tipų parsing'as (skyriaus eilutės, tuščios kainos, dubliuotas kodas, kodas skaičius/tekstas).
- Kainos: cost = export × (1+SALE), price = export × 1,5, discount = round(−SALE×100); be SALE – be nuolaidos.
- Nerastas kitame Stock faile kodas → 0 (ir PS prekėms); klaidingas failas → nieko.
- `New 2027` → badge; dingus – nuimamas.
- PS re-key: SKU, URL, sandėlis; kitų tiekėjų prekėms – tik sandėlis, kaina nepakeista.
- Neatpažintas failas / klaidingos eilutės → likučiai neliečiami.
- Gamintojo modulis: fixture HTML → kodai, variantai, nuotraukos, specs; 429/timeout → be duomenų, importas nekrenta.
- Pakartotinis importas → 0 pakeitimų (`exported_hash` stabilus).

## Variantų grupavimas (adapteris)

`AlbatrosAdapter.iter_items` po Stock nuskaitymo: `manufacturer_lookup()` →
`apply_manufacturer_content()` (išjungiama feed config `manufacturer_groups: false`).

- Kodas randamas `ManufacturerContent.codes` pagal `slugify(brand)`; keli puslapiai → EN pirma,
  tada puslapis su mažiausiai kodų.
- Puslapis = bazinė prekė: `extra.product_group = "<ženklas>:<url slug>"`, `base_name = series`,
  ašys = puslapio eilutės laukai (be `name`), EAN iš `specs.eans`, nuotraukos = Stock FOTO + gamintojo.
- Grupės variantams su vienodomis ašimis (sportfishtackle eilutės be ašių) `model` = Stock
  pavadinimo žodžių skirtumas (`Exorus 1000 … 210g` / `Exorus 2500 … 260g` → `1000 210g` / `2500 260g`).
- EAN, kurį šaltinis duoda keliems kodams, nesiunčiamas (kitaip pipeline sujungtų skirtingas prekes); unikalus EAN sujungia su kito tiekėjo preke (Albatros = papildomas sandėlis).
- Jei gamintojo turinys atsiranda **po** importo (pvz. Mepps), esami offer'iai lieka susieti su
  senomis prekėmis: po `scrape_manufacturer` + importo paleisti
  `relink_variant_products --supplier albatros` (dry-run) → `--apply`.
- Kodas be gamintojo puslapio (vartotojo sprendimas „kol kas kaip turime“, publikuojama ir be
  nuotraukos – vėliau peržiūrima rankiniu būdu) – Stock pavadinimas ir FOTO, papildomai:
  - **zuklys.lt** (`scrape_manufacturer --source zuklys [--codes-file]`, vartotojas leido):
    kodas ieškomas `sitemap.xml` prekių URL slug'uose (robots.txt draudžia
    `route=product/search`), puslapis priimamas tik jei JSON-LD `name` prasideda kodu.
    `ManufacturerContent(source="zuklys", language="lt")`; adapteris ima tik nuotraukas ir
    parametrų lentelę (`attributes.specs`), **negrupuoja**. Be `--codes-file` – `albatros` offer'ių
    kodai be jokio turinio. 1 sitemap + ≤3 puslapiai kodui (tik slug'o atitikmenims; ~3 000 iš 7 573).
    Lokalus rezultatas (Stock 10.21): 2 721 zuklys puslapis, 2 241 prekė su zuklys turiniu; be nuotraukos
    liko 2 447 prekės iš 7 246 (buvo 3 464). Daugiausia be nuotraukos: Mepps 550, Crazy Fish 367,
    Rapala 340, Lucky Craft 210, Borika 161, Kuusamo 94, Savage Gear 86, RAM Mounts 84, Stroft 77.
  - **nemus.lt** (`scrape_manufacturer --source nemus`, vartotojas leido; Albatros el. parduotuvė,
    vienintelė Jobe / Humminbird / Minn Kota / Borika tiesioginė): sitemap.xml -> **visas katalogas**
    (~1 500 puslapių, robots.txt leidžia; paieška uždrausta). Kodas = JSON-LD `model` (= Albatros kodas).
    Saugoma: 1000×1000 nuotraukos, LT aprašymas (`description`, prekėje dar nenaudojamas – jau LT),
    `specs.offer` = {price su PVM, in_stock, brand}. Prekių URL be skyriaus (`nemus.lt/xo-explr-9`),
    todėl `specs.category` = breadcrumb iš kategorijų puslapių (sitemap'e, su `?page=N`), giliausia,
    jei prekė keliose. Ženklas: pavadinimas -> `offer.brand` per aliasus (Minnkota -> Minn Kota). Pauzė ≥2,5 s,
    `Connection: close`. Pilnas nuskaitymas be klaidų -> dingusių puslapių `codes=[]` (nebeparduodama).
    Adapteris (`nemus_catalog`, numatyta kaip `manufacturer_groups`), vartotojo sprendimai:
    - kodas ir Stock faile: savikaina iš Albatros; `rrp = min(export × 1,5, nemus / 1,21)`,
      galutinė `min(export × (1 + SALE) × 1,5, nemus / 1,21)` -> `discount_percent` perskaičiuojamas;
      jei `export` tuščias – kaip nemus-only. Likutis – iš Stock. Faktas (Stock 10.21, 649 kodai):
      Jobe / Humminbird / Minn Kota ir kt. `export` = nemus kaina be PVM (mažmeninė), SALE -0,2 =
      dilerio nuolaida -> `× 1,5` būtų +50 % virš rinkos; `min()` palieka nemus kainą, marža 20 %.
    - kodo Stock faile nėra: atskira `albatros` prekė, `rrp = nemus / 1,21`, savikaina `× 0,8`,
      likutis 19 (Centrinis sandėlis), jei nemus „yra“, kitaip 0. Be kainos – praleidžiama.
    - variantai (`apply_nemus_groups`, po gamintojo grupių, prieš FOTO ir PS): tas pats ženklas +
      pavadinimas iki paskutinio kablelio, ≥ 2 kodai -> `product_group = "nemus:<slug>-<sha1[:8]>"`,
      ašis `size` = dalis po kablelio ('..., L', 'dydis 10', 'geltona 60-90 kg' – skaido export'as).
    - Retail (nuotraukos) `RETAIL_SOURCES = ("nemus", "zuklys")`: nemus pirmas, jei turi nuotraukų.
    - Laivai / kateriai kol kas kaip įprastos prekės (Plane BZ: atskiros kategorijos ir užklausos forma).
  - **Bendra FOTO** (`apply_photo_groups`, išjungiama `photo_groups: false`): tas pats ženklas +
    skyrius + Stock FOTO → `product_group = "<ženklas>:photo:<sha1[:12]>"`, `base_name` = bendra
    pavadinimo pradžia, ašis `model` = pavadinimo skirtumas. Nejungiama, jei bendra pradžia tik ženklas
    (bendra kategorijos nuotrauka).
  - rockworld.lt pagal kodą neranda; zuklesreikmenys.lt → zvejybosreikmenys.lt už Cloudflare (403 ir iš srv2/ES – blokuojami DC IP)
    challenge (neapeinama).
- **Rankinis PS grupavimas svarbiausias** (vartotojo sprendimas): feed config
  `fixed_groups: {kodas: "ps:<BE product id>"}` (arba `{"group": …, "base_name": …}`) perrašo
  gamintojo ir FOTO grupę. Užpildoma iš BE atitikmenų (617 kodų, 231 PS prekė) prieš pirmą importą.
- Eilė: gamintojo turinys → zuklys → FOTO grupės → `fixed_groups` → `_distinguish_twins`.

Dry-run su `Stock 10.21` (lokaliai, be BE rašymo):

| | Kiekis |
|---|---|
| Kodai | 10 398 |
| Prekės | 8 305: 430 su keliais variantais, 302 vieno varianto su gamintojo turiniu, 7 573 be gamintojo turinio |
| Kodai gamintojo grupėse | 2 825 (visi su nuotrauka; 669 iš 732 puslapių su aprašymu) |
| Be gamintojo turinio ir be nuotraukos | 3 530 (Crazy Fish 1 118, Mepps 558, Lucky Craft 375, Rapala 374, Borika 161 …) |
| BE atitikmenys (prod, read-only) | 820 variantų: PS 617 (231 prekė) → re-key; Fox/Shimano/Korda 203 → tik sandėlis |

Su FOTO grupėmis (2-as dry-run): 7 244 prekės; 357 FOTO grupės (1 418 kodų; Savage Gear 219,
Jobe 47, DAM 21), 6 155 vienetinės be gamintojo turinio.

TiCA: BE'je 47 Ockert (`OC.<modelis>`) + 9 PS variantai. Albatros TiCA kodai su Ockert modeliu
pavadinime – tik 2 ritės (146610 `GBAT8000`, 146612 `GLAT5000`; dar 2 – ričių dangteliai, ne tos
prekės). Kitų ženklų tokių atitikmenų nėra, todėl taisyklės nėra – susieti rankiniu būdu.

Peržiūrai (ne automatiškai):
- 96 PS prekės išsiskaido į kelias Albatros prekes (64 – tik kodai be gamintojo turinio,
  29 – dalis gamintojo grupėje, 3 – kelios gamintojo grupės).
- 13 gamintojo grupių atitinka kelias PS prekes (PS dublikatai → sujungti su redirect'u).
- `GRD230`, `GRD231` atitinka ir `FX.`, ir `KD.` variantą.

## Kas toliau (eilės tvarka)

1. Supplier `albatros` (prefiksas `AL.`, `selling_price_includes_vat=False`, warehouse
   `albatros` = „Centrinis sandėlis“) + upload feed'as (role `catalog`, adapter `albatros`);
   **jokių `PriceRule` šiam tiekėjui** – kaina jau RRP bazėje (export × 1,5).
   Variantų grupavimas – iš gamintojų puslapių (2 žingsnis); `extra.photo_group` (bendra FOTO)
   – užuomina grupavimui.
2. Gamintojų scraper'iai: ✅ Savage Gear, DAM, Prologic, Madcat (žr. „Įgyvendinta“). Toliau:
   Mepps šaltinis, Rapala/Crazy Fish (modelio atitikimas), padengimo ataskaita pagal Stock kodus.
3. Dry-run ataskaita vartotojui (kiekiai, kainų pavyzdžiai, susiejimas su esamomis prekėmis).
4. Tik po patvirtinimo: BE backup → PS re-key → eksportas → Typesense patikra.

Atvirų verslo klausimų nėra; neaiškumai – klausti vartotojo, ne spėti.
