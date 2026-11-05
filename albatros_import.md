# Albatros integracija (planas agentams)

> Statusas: **analizė, kodo dar nėra.** Šis failas – nurodymai lokaliems ir Devin agentams.

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
| **Stock (pagrindinis, pilnas)** | `Stock 10.21.xlsx`: 10 399 kodų (6 588 su 0), 110 skyrių | A žymė (`""` / `New 2027`), Code, Item, `be PVM, export`, `SALE`, `Q-ty`, Order, Catalogue, `FOTO` | **Katalogas + savikaina + likučiai.** Importuojama **viskas**, įskaitant 0. Kito Stock importo metu nerastas kodas → likutis 0 |
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
- BE kainų logika jau palaiko `InventoryItem.offer_discount_percent` (`catalog/services/money.py`,
  `home_services.py`). **Trūksta:** BE `catalog_import` jo neįrašo, normalyzer jo nesiunčia →
  reikia papildyti normalyzer payload'ą (per sandėlį: `discount_percent`) ir BE `_apply_stock` /
  katalogo importą.

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
  - Dabar normalyzer siunčia vieną kainą variantui (`_item_price_net` = pirminio offer'io kaina).
  - BE jau turi per-sandėlio laukus `InventoryItem.cost_eur` ir `offer_price_override_eur`,
    bet importas jų nepildo.
  - Reikia atskiro PR: normalyzer stock payload'e kiekvienam sandėliui siunčia `qty`, `price_net`
    ir `cost_net`; BE `_apply_stock` juos rašo į `InventoryItem`; frontas renkasi pigiausią
    sandėlį, kuriame yra likutis, ir rodo jo pristatymo laiką.
  - Nuolaida skaičiuojama nuo pasirinkto sandėlio kainos.

## Naujienų badge („New 2027“)

Šaltinis: Stock failo A stulpelis `New 2027` ir (arba) kodas yra `2027 NEW.xlsx`.
BE dabar prekių badge'ų nėra (`badge` yra tik CMS blokuose). Variantai:
1. **Feature** `Naujiena = 2027`: veikia per esamą importą ir filtrus, bet frontui reikia
   taisyklės, kad šį feature rodytų kaip badge.
2. **Siūloma:** BE `Product.badges` (JSON sąrašas, pvz. `["new"]` + `badge_until`);
   importas jį pildo, frontas rodo, Typesense indeksuoja filtravimui. Reikia BE + FE PR.
Kai žymė dingsta iš failo, badge nuimamas.

## Gamintojų šaltiniai (vartotojo pateikti)

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

## Atviri klausimai

1. Naujienų badge: feature ar `Product.badges` (BE + FE).
2. Per-sandėlio kainų PR (normalyzer + BE + FE) – daryti prieš Albatros eksportą ar po jo.
