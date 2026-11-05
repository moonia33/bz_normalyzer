# AGENTS.md – nurodymai agentams (lokaliems ir Devin)

Pradėk čia. Šis failas sako, **kas jau veikia, kur dokumentacija ir ko neliesti**.
Kalba su vartotoju (Ramūnas) – lietuviškai.

## Sistema (3 repo)

| Repo | Vaidmuo |
|---|---|
| `moonia33/bz_normalyzer` (šis) | Tiekėjų importas → normalizavimas → LT vertimas → eksportas į BE |
| `moonia33/ecommercebackend` | Django BE: katalogas, sandėliai, kainos, badge'ai, Typesense, ops API |
| `moonia33/bz_sveltefront` | SvelteKit frontas (importai per `#lib/...`, ne `$lib`) |

Srautas: **importas → vertimas → eksportas į BE → Typesense indeksas**. Tikrinama kartą per
savaitę naktį, tiekėjai išskaidyti per skirtingas naktis (`SupplierSchedule`), kainos ir
likučiai – deltomis kasdien/periodiškai. Prioritetas: **kokybė svarbiau už kainą**.

## Dokumentacija

- `README.md` – architektūra, adapterių kontraktas, komandos.
- `catalog_import.md` – BE katalogo formatas (kanoninė versija: ecommercebackend `docs/normalizer_integration.md`).
- Tiekėjai: `bounty_fishing_import.md`, `mivardi_import.md`, `forgotten_flavours_import.md`,
  `holdcarp_import.md`, `ridgemonkey_import.md`, **`albatros_import.md` (vykdomas darbas)**.
- `prestashop_one_time_import.md` – senų PS prekių perkėlimas (re-key) ant normalyzer'io.
- BE pusė: ecommercebackend `docs/normalizer_integration.md`, `docs/badges.md`, `docs/search.md`, `docs/enrichment.md`.

## Tiekėjai (prefiksas → būsena)

Veikia prod'e su tvarkaraščiais: Fox `FX.`, Moss `MS.`, Bounty `BT.`, Mivardi `MV.`, Ockert `OC.`,
Shimano `SH.`, Korda `KD.`, Forgotten Flavours `FR.`, HoldCarp `HC.`, RidgeMonkey `RM.`.
Kuriamas: **Albatros `AL.`** – žr. `albatros_import.md` (būsenos lentelė viršuje).

## Kas neseniai pasikeitė (2026-11)

- **Per-sandėlio sąlygos** (#35): `Offer.discount_percent`, `Offer.shop_variant_code`,
  `offer_terms()`. Ta pati prekė keliuose sandėliuose: pirminis offer'is nustato kainą,
  papildomas siunčia tik likutį + sandėlio kainą. BE rodo pigiausią sandėlį su likučiu.
- **Badge'ai BE** (ecommercebackend #14): kuriami admine, priskiriami rankiniu būdu, taisyklėmis
  arba importu. Normalyzer: `NormalizedItem.badges` → `Offer.badges` → prekės `badges`
  (`None` = raktas nesiunčiamas, shop'e nieko nekeičia).
- Variantų ašys/feature'ai eksportuojami deterministiškai → antras dry-run iš eilės turi rodyti 0 pakeitimų.
- Tiekėjo pasikartojantis EAN skirtingiems kodams nejungia variantų ir į BE nesiunčiamas.

## Nekeisti (saugikliai)

- `fetch_errors` ir `skipped_partial_missing` (`apps/ingestion/pipeline.py`): klaida ≠ likutis 0.
- `mark_absent_offers`: nulina tik po validaus pilno importo.
- `_product_targets`, `_write_product_text` (`translate_lang.py`), `_propagate_product_text`.
- `sorted(all_axes)` `_plan_variants` ir `_shared_eans` (`apps/catalog_export/services.py`).
- `plan_prices()` praleidžia `shop_variant_code` offer'ius; `price_locked` niekada nesiunčiamas.

## Darbo taisyklės

- Kiekvienas pakeitimas – PR; **nemerginti be vartotojo „merginti“**.
- Prieš eksportą į BE: `--dry-run` ir ataskaita vartotojui (kiekiai, kainų pavyzdžiai, mapingas).
- Prieš BE duomenų keitimą (re-key, masinis išjungimas): BE DB backup.
- Nesumapinta kategorija → `nepriskirta`, ne spėjimas. Neaiškūs atitikmenys → CSV peržiūrai.
- Po eksporto patikrinti BE ImportRun klaidas ir Typesense dokumentų skaičių.
- BE Typesense schemos pakeitimas (naujas laukas) prod'e užtrunka: po deploy'aus patikrinti
  `num_documents`; jei 0 – `manage.py reindex_products` (~8 min).
- Testai: `pytest` (žinomas baseline fail: `ApiTests::test_lists_only_published_offers`, 401).
- Slaptažodžių/raktų neįrašyti į repo ar logus.
