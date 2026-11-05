# Albatros integracija (planas agentams)

> Statusas: **analizė, kodo dar nėra.** Šis failas – nurodymai lokaliems ir Devin agentams.
> Kainodara dar **nenuspręsta** – neįgyvendinti, kol vartotojas nepatvirtins.

## Pagrindiniai faktai

- Tiekėjas `albatros`, shop SKU prefiksas `AL.` (`AL.<kodas>`).
- Albatros – tame pačiame mieste, **vietinis sandėlis**. BE warehouse `albatros`,
  fronte pavadinimas **„Centrinis sandėlis“**.
- Jokio API/feed'o: visi duomenys ateina **Excel failais**, kuriuos įkelia vartotojas.
  Likučių ir kainų sinchronizacija vyksta **tik įkėlus failą** (jokio tvarkaraščio,
  jokių užklausų tiekėjui).
- Excel'yje tik: kodas, pavadinimas EN, kaina be PVM, likutis. Kategorijų nėra –
  tik **skyriaus** antraštė (eilutė, kur užpildytas tik A stulpelis, pvz. `Bait / RAPALA`,
  `Reel - Ritė`). Skyrius = orientacija į stambesnę kategoriją.
- Turinys (aprašymai, nuotraukos, specifikacijos, variantų grupavimas) – **scrap'inamas
  iš gamintojų svetainių pagal kodą**.

## Failų tipai

| Tipas | Pavyzdys | Stulpeliai | Rolė |
|---|---|---|---|
| Raw Stock (pilnas) | `Raw Stock 10.26.xlsx` (~30 800 kodų, 116 skyrių) | A skyrius, B kodas, C pavadinimas, D kaina be PVM, E likutis | **Katalogas + likučiai + kainos.** Pilnas sąrašas → leidžiama nulinti dingusius |
| Stock (su likučiu) | `Stock 10.21.xlsx` (~10 400) | Code, Item, be PVM export, SALE, Q-ty, Order, Catalogue, FOTO | Papildomi **nuotraukų URL** (images.salsify.com, albatros.lt, jobesports.com) ir SALE nuolaida |
| Naujienos | `2027 NEW.xlsx` | Code, Item, su PVM, export, nuolaida, FOTO | Naujos prekės, nuotraukos; **nenulina** kitų |
| Gamintojo kainoraščiai | `TiCA 2024`, `Crazy Fish Softlures 2024`, `Outdoors Adventures 2024` (Humminbird/Jobe/RAM: EAN, RRP, foto) | skiriasi | Papildomi duomenys (EAN, RRP, foto); **nenulina** |

Taisyklės:
- Kodą normalizuoti: `str(x).strip()`, skaičius be `.0`, išlaikyti brūkšnius (`10005-100`).
  Dubliuotas kodas faile (1 atvejis) – imti paskutinę eilutę, užfiksuoti įspėjimą.
- Tuščia kaina (~1 300 kodų, 35 su likučiu) → prekė nepublikuojama, bet esamai prekei
  likutis atnaujinamas.
- Failo tipą atpažinti pagal antraštes, ne pagal failo vardą. Neatpažintas → klaida, nieko nekeisti.
- Lietuviški bendriniai pirmi žodžiai (`VOBLERIS`, `BALANSYRAS`, `SUKRIUKĖ`, `VARIKLIS`,
  `BLIZGĖ`, `GUMINUKAS`) nėra ženklas: ženklas = kitas žodis arba iš skyriaus
  (`VOBLERIS RAPALA BXB-3 BGH` → Rapala). Santrumpos: `SG` = Savage Gear, `PL` = Preston?
  (patikrinti), `R.T.`/`RT` = Ron Thompson, `CC` = CC Moore, `FZ` = Effzett.

## Apimtis (vartotojo sprendimas)

- Importuoti **tik su likučiu > 0**.
- **Išimtis:** jei pagal gamintojo duomenis kodas yra prekės variantas ir **bent vienas**
  tos prekės variantas turi likutį – importuojami **visi** jos variantai (kiti – su 0).
- Vėliau likutis dingsta → prekė lieka, likutis 0 (netrinama).

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
- Atviras klausimas: **kaina, kai tą pačią prekę turi keli tiekėjai** – nespręsti, kol vartotojas
  nenuspręs. Kol kas kitų tiekėjų prekėms Albatros duoda tik likutį, kainos neperrašo.

## Gamintojų šaltiniai (vartotojo pateikti)

Patikrinta iš Devin VM (paieška pagal Albatros kodą):

| Ženklas | Kodų / su likučiu | Šaltinis | Paieška pagal kodą |
|---|---|---|---|
| Savage Gear (`SAVAGE`, `SG`) | 6 124 / 3 452 | savagegear.com (Shopify); sportfiskeprylar.se | ✅ `/search?q=<kodas>&type=product` randa |
| DAM | 2 320 / 911 | dam.de | ✅ `/search?q=<kodas>` randa |
| Mepps | 1 424 / 556 | store.mepps.com; sportfiskeprylar.se | ✅ `/search?q=<kodas>` randa |
| Prologic | 715 / 434 | prologicfishing.com; sportfiskeprylar.se | ⚠ HTTP 429 (rate limit) – lėtai, iš srv2 |
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
   Raw Stock failas (FeedRole `catalog`, upload); skyrius → `category_raw`;
   kodas → `external_id`, `sku=AL.<kodas>`; kaina be PVM → pirkimo kaina
   (`purchase_price_includes_vat=False`); likutis → warehouse `albatros`.
   Papildomi failai (Stock su FOTO, NEW, kainoraščiai) – atskiri feed'ai (`partial_update`),
   papildo nuotraukas/EAN/RRP, **nenulina**.
2. **Apimties filtras:** su likučiu + kiti tos pačios gamintojo grupės variantai.
3. **Gamintojo turinys:** `scrape_manufacturer --source <x>`; susiejimas pagal kodą
   (`ManufacturerContent.codes`), grupavimas į bazinę prekę + variantus.
4. **Esamų prekių susiejimas:** PS → re-key, kiti tiekėjai → tik sandėlis.
5. **Kategorijų mapingas** `map_albatros_categories.py`: 116 skyrių → BE kategorijos,
   nesumapinta → `Nepriskirta`. Per stambius skyrius (`Bait / Wobbler`) tikslinti pagal
   gamintojo kategoriją.
6. **Vertimas į LT** (`translate_lang`, ta pati schema).
7. **Eksportas:** `export_catalog --supplier albatros --dry-run` → ataskaita → `--push`;
   BE ImportRun be klaidų; **Typesense** reindex (`search.services.index_products`) ir patikra.
8. **Likučiai:** naujas Raw Stock failas → importas → `export_stock`.
   `mark_absent_offers` nulina dingusius tik jei importe 0 klaidų ir matyta ≥ `ABSENT_MIN_SEEN_SHARE`.

## Saugikliai (nekeisti)

- Klaida faile ≠ likutis 0 (`mark_absent_offers`).
- `fetch_errors` logika `apps/ingestion/pipeline.py`.
- `_product_targets`, `_write_product_text` (`translate_lang.py`), `_propagate_product_text` (`pipeline.py`).
- `sorted(all_axes)` ir `_shared_eans` (`apps/catalog_export/services.py`).
- Nemerginti be vartotojo patvirtinimo. Prieš BE pakeitimus – backup.

## Testai (privalomi prieš PR)

- Visų failų tipų parsing'as (skyriaus eilutės, tuščios kainos, dubliuotas kodas, kodas skaičius/tekstas).
- Apimtis: variantas be likučio importuojamas, jei grupėje yra su likučiu.
- PS re-key: SKU, URL, sandėlis; kitų tiekėjų prekėms – tik sandėlis, kaina nepakeista.
- Neatpažintas failas / klaidingos eilutės → likučiai neliečiami.
- Gamintojo modulis: fixture HTML → kodai, variantai, nuotraukos, specs; 429/timeout → be duomenų, importas nekrenta.
- Pakartotinis importas → 0 pakeitimų (`exported_hash` stabilus).

## Atviri klausimai

1. Kainodara (antkainis nuo pirkimo kainos be PVM? RRP? SALE stulpelis?).
2. Kaina, kai tą pačią prekę turi keli tiekėjai.
3. `PL` – Preston ar kitas ženklas?
4. Ar Raw Stock visada pilnas (visi kodai, ir su 0)? Nuo to priklauso dingusių nulinimas.
