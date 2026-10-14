# Katalogo importo dizainas (normalizuotas failas → shop)

Statusas: **įgyvendinta** (2026-10-15) — `catalog_import` app: modeliai, schemas, service, komandos, ops API, testai.

## 1. Dabartinės būsenos vertinimas

### PrestaShop importas — laikinas, pakeisti

`catalog/management/commands/import_prestashop_*.py` skaito phpMyAdmin JSON dump'us (`ps_*.json`). Problemos:

- **Variantai visai neimportuojami** — prekės su `ps_product_attribute` eilutėmis praleidžiamos (`skipped_has_combinations`).
- Visas importas vienoje `transaction.atomic()` — viena bloga eilutė numuša visą run'ą, nėra partial success.
- Nėra jokio audit'ų/run'ų tracking: nei kas importuota, nei kas nepavyko, nei kada.
- `external_presta_id` — šaltinio-specifiškas pavadinimas įkodintas į `Product`/`Variant`/`Category`/`Brand`.
- SKU fallback `PS-{id}` — dirbtiniai kodai; naujas flow naudoja realius kodus.
- Paveikslėliai neimportuojami (`ps_image.json` yra, bet komandos nėra).
- Kategorijų/brand'ų mapping'as tik per `external_presta_id` — naujam šaltiniui netinka.

### Modelio branduolys — tvirtas, palikti

- **Kiekviena prekė turi ≥1 `Variant`** — teisingas invariantas (kaina/likutis/krepšelis/checkout visur per variant). Prekė be variantų = vienas "default" variantas su `sku = product.code`. Niekada neeksponuoti "producto be varianto" — FE visada gauna `variant_id`.
- `OptionType`/`OptionValue` + `VariantOptionValue` + `ProductOptionType` — pilna variantų su opcijomis schema, paruošta, tik nenaudota.
- `Feature`/`FeatureValue` + `ProductFeatureValue` — facets'ai paruošti.
- `ProductGroup` (`code` unique) — tiesiogiai atitinka "prekės grupės kodas".
- `*Translation` modeliai su per-lang unikaliais slug'ais — paruošta normalizerio tekstams.
- `InventoryItem` (variant×warehouse) su offer pricing — likučių feed'as rašo čia.

### Modelio trūkumai, kuriuos verta pataisyti dabar

| Problema | Sprendimas |
|---|---|
| `external_presta_id` pririštas prie PrestaShop | Pakeisti į generic `import_source` + `import_code` (unique together) ant `Product`, `Variant`, `Category`, `Brand` |
| Nėra import run/item audit | Nauji modeliai `ImportRun` + `ImportItem` (žr. §4) |
| `Variant.barcode` neindeksuotas, EAN kartojimui neužtikrintas | `null=True, unique=True` (tuščias → NULL); matching pagal EAN kaip antrinis raktas |
| `group_code` faile ≠ `ProductGroup` | `ProductGroup` yra kolekcijų/stiliaus grupavimas — neliesti. Naujas `product_group_code` laukas ant `Product` **ir** `Variant` (CharField, indexed, optional) — laisvas kodas iš failo, be FK |
| Manual ėjimas per admin'ą | `Variant`/`ProductImage` inline'ai `Product` admin'e (patikrinti/pagerinti atskirai) |

## 2. Formatas: JSON, ne XML — ir ne "API arba failas", o abu

Abi sistemos mūsų (normalizeris = atskiras Django servisas) — nėra jokios priežasties versti XML. JSON su schema-validacija duoda tą patį, kas XSD, su 5× mažiau kodo.

**Vienas canonical formatas, du pristatymo kanalai (realizuota):**
1. `manage.py import_catalog <file> [--dry-run] [--no-apply]` — cron / ops
2. `POST /api/v1/ops/imports/catalog` (JSON body, `?dry_run=`/`?apply_async=`) + `GET /api/v1/ops/imports/runs*` — normalizeris push'ina tiesiai

Jei katalogas viršytų ~20-50k eilučių — NDJSON (viena eilutė = viena prekė, stream parsing). Iki tol — paprastas JSON dokumentas.

### Failo struktūra (v1)

```json
{
  "format_version": 1,
  "source": "normalizer",
  "mode": "upsert",
  "generated_at": "2026-10-14T10:00:00Z",
  "items": [
    {
      "code": "ABC-123",
      "product_group_code": "GR-77",
      "ean": "4770000000000",
      "brand_code": "acme",
      "category": "tools-drills",
      "tax_class": "standard",
      "is_active": true,
      "price_net": "19.99",
      "cost_net": "10.00",
      "weight_g": 1200,
      "translations": {
        "lt": {"name": "...", "slug": "...", "description": "...", "seo_title": "", "seo_description": ""},
        "pl": {"name": "...", "slug": "...", "description": "..."}
      },
      "features": [{"code": "color", "value": "red"}],
      "images": [{"url": "https://...", "alt": "", "sort_order": 0}],
      "variants": [
        {
          "code": "ABC-123-M",
          "product_group_code": "GR-77-M",
          "ean": "4770000000001",
          "options": {"size": "m"},
          "price_net": "21.99",
          "stock": {"main": 5}
        }
      ],
      "stock": {"main": 10}
    }
  ]
}
```

Taisyklės:

- `variants` tuščias arba nėra → sukuriamas default variantas `code = item.code`.
- `translations.lt` privaloma (bazinė kalba → `Product.name`/`description`; likusios → `ProductTranslation`). Trūksta → item klaida.
- `mode`: `upsert` (default, nieko netrina) / `full_sync` (+`delete_missing:true` — dėjaudina neatsiųstus; dev/test only, niekad default).
- `stock` galima pridėti prie prekės ir varianto; raktas = `Warehouse.code`. Tas pats variantas gali turėti likučius keliuose sandėliuose (skirtingi tiekėjai → skirtingi warehouse'ai).
- `code` — pagrindinis matching raktas (mūsų SKU, `Variant.sku` unique). `ean` → `Variant.barcode` (unique; tuščias saugomas kaip NULL).
- `product_group_code` — laisvas tekstinis laukas `Product.product_group_code` **ir** `Variant.product_group_code` (indeksuota). `ProductGroup` neliečiamas — tai atskiras "collections"-tipo grupavimas. Variantas turi savo grupės kodą šalia `sku`+`ean`.
- `category` — normalizeris siunčia shop kategorijos `slug` (map'as normalizeryje, čia tas pats kategorijų rinkinys). Nežinomas slug → item ERROR (arba `create_missing_taxonomy` flag'as).
- `price_net`/`cost_net` — visada **be PVM** → `Variant.price_eur`/`cost_eur`. B2B kainos faile nereikalingos — jos daromos per `ShopPriceRule` taisykles.

### Likučių feed'as (atskiras, lengvesnis)

```json
{"format_version": 1, "warehouse_code": "main", "items": [{"code": "ABC-123", "qty": 10}]}
```

Atnaujina tik `qty_on_hand`, niekada neliečia `qty_reserved`. Tas pats ImportRun mechanizmas. `POST /api/v1/ops/imports/stock` + `manage.py import_stock <file> [--warehouse X]` + rankiniu admin.

## 3. Vertimai normalizeryje — TAIP, teisingas sprendimas

- Deterministiška, peržiūrima prieš siuntimą, viena vieta MT + žmogaus redagavimui.
- Shop'e nieko neverčiam — tik saugom. Shop validuoja `language_code` prieš `SUPPORTED_LANGUAGE_CODES` (esamas helper `_normalize_and_validate_translation_language_code`).
- Normalizeris siunčia `slug` hint'us per lang; shop užtikrina unikalumą (kolizijoje suffix `-{code}`, esamas pattern'as).
- `Feature`/`OptionValue` vertimai taip pat iš failo: `features[].translations`, `options.*.translations` (neprivaloma — fallback į bazinį).

## 4. Import pipeline (atsparumo klaidoms branduolys)

Niekada ne rašyti į `Product`/`Variant` tiesiogiai. **Stage → Apply:**

```
failas → parse → per-item schema validacija → ImportItem(status)
       → apply (kiekvienas item savo tx) → ataskaita
```

Modeliai (nauja app `catalog_import` arba `catalog` plėtinys):

- `ImportRun`: `kind` (`catalog`/`stock`), `source`, `file_name`, `file_sha256`, `status` (QUEUED→PARSING→APPLYING→DONE/FAILED/DONE_WITH_ERRORS), `total/ok/failed/skipped`, `options` (dry_run, create_missing_taxonomy…), `triggered_by`, timestamps.
- `ImportItem`: `run` FK, `row_no`, `external_code`, `payload` JSON, `status` (OK/ERROR/SKIPPED), `error` text, `product_id`/`variant_id` po apply.

Taisyklės:
- **Per-item `transaction.atomic()`** — viena bloga eilutė = ERROR item'as, run tęsia.
- **Idempotencija**: rerun to paties failo = no-op (diff pagal field'us; `file_sha256` dedupe → nuoroda į esamą run).
- **Dry-run**: `--dry-run` / `dry_run:true` — pilna validacija + "kas pasikeistų" ataskaita, nulis write'ų.
- Nežinomi reference'ai (`brand_code`, `category_code`, `tax_class`): default → item ERROR su aiškiu pranešimu; flag `create_missing_taxonomy` leidžia auto-create'ą.
- Apply fazė — Celery task (infra jau yra); run statusas stebimas per ops API + admin.
- `file_sha256` + `source` unique per run — tas pats failas du kartus = tas pats run.

## 5. Darbų planas (prioritetų tvarka)

1. **Modelių pataisymai** (viena migracija):
   - `import_source` + `import_code` ant `Product`, `Variant`, `Category`, `Brand` (`external_presta_id` palikti kol perkelti duomenys, tada drop atskirai).
   - `Variant.barcode db_index`; EAN-13 checksum validacija `clean()` (soft — tik warning'as importe).
   - `ImportRun`, `ImportItem` modeliai.
2. **Formato spec + schema**: `docs/catalog_import_format.md` (ši ataskaita kaip pagrindas) + `catalog_import/schemas.py` (pydantic).
3. **Importer service** `catalog_import/services.py`: parse → validate → upsert → report (per-item tx, slug unikalumas, translations, options/variants, features, images iš URL per esamą image pipeline).
4. **Entry points**: `manage.py import_catalog`, `POST /api/v1/ops/catalog-imports` (OpsJob-style async), admin import form.
5. **Stock feed**: `manage.py import_stock` + `POST /api/v1/ops/inventory-imports`.
6. **Testai**: fixture failai, idempotencija (2× tas pats failas), error-rows, variant/non-variant, dry-run, partial taxonomy.
7. **Po stabilizacijos**: PrestaShop import komandų deprecacija (palikti kol senų duomenų migracija baigta), `catalog/api.py` (917 eil.) skaldymas.

## 6. Patvirtinti sprendimai (2026-10-14)

- `group_code` ≠ `ProductGroup` (collections) → `product_group_code` laukas ant `Product` ir `Variant`.
- EAN unikalūs → `Variant.barcode unique` (tuščioms reikšmėms NULL).
- Skirtingi tiekėjai → skirtingi warehouse'ai; ta pati prekė kelių tiekėjų = tas pats variantas, keli `InventoryItem`. Tas pats `code` iš skirtingų šaltinių → konfliktas, žymimas peržiūrai (nesilenkti silently).
- `price_net` visada be PVM.
- Kategorijų mapping normalizeryje → faile shop `Category.slug`.
- B2B kainos per `ShopPriceRule` — faile tik retail base `price_net`.
