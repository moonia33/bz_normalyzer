# PrestaShop one-time import — handoff

## Tikslas

Vienkartinis katalogo/likučių migravimas iš gyvos PrestaShop (`baltijoszvynas.lt`)
į šį Django backend. Prekės ~5.2k, kombinacijos ~8.4k, nuotraukos ~27k.
Naudojamas egzistuojantis canonical import pipeline (`catalog_import` app) —
**nerašome** tiesioginio SQL/ORM importo.

## Kur kas yra

- **Konverteris** (normalyzerio repo `bz_normalyzer`):
  `apps/catalog_export/management/commands/export_prestashop.py`
  — phpMyAdmin JSON dump'ai → canonical `catalog.json` + `stock.json` + `match_report.json`.
- **Input duomenys** (normalyzerio repo, `docs_suplier/prestashop_one_time/`):
  direktorija yra gitignore'intyta (doc'uose buvo credential'ų) — failai egzistuoja
  tik lokaliai ir turi būti perduoti kartu su užduotimi:
  `ps_product.json, ps_product_attribute.json, ps_stock_available_without_combination.json,
  ps_image.json, ps_category_id.json, ps_manufacturer.json,
  shop_variants.csv, shop_brands.csv, shop_categories.csv, supplier_brand_prefix.csv`.
- **Shop import pipeline**: `catalog_import/services.py`,
  `manage.py import_catalog|import_stock`, ops API `POST /api/v1/ops/imports/*`.

## Jau padaryta shop'e (local dev DB)

- `Warehouse code="local"` (kind=local) — PrestaShop fizinės parduotuvės likučiai.
  **Prod'e sukurti prieš importą** (admin arba shell):
  `Warehouse.objects.get_or_create(code="local", defaults={"name":"Parduotuvė (local)","kind":"local","country_code":"LT"})`
- `Category slug="nepriskirta"` — fallback root/pagrindinis/nemappinguotoms prekėms.
  Prod'e analogiškai sukurti.
- `catalog_category.import_code` jau laiko PrestaShop `id_category` — kategorijų
  map'as gaunamas nemokamai.

## Konverterio politika (`export_prestashop`)

1. **Matching**: `ps_product_attribute.reference` → `ps_product.reference`
   lyginami su `catalog_variant.sku` tiesiogiai IR su nuimtu tiekėjo prefiksu
   (`FX.`,`KD.`,`MS.`,`SH.`). EAN netikrinamas (PrestaShop'e tuščias).
2. Visi vienetai matched → **nesikuria** produktas, tik `stock.json` eilutės.
3. Dalinis match → matched vienetai į `stock.json`, likusieji į
   `match_report.review` (žmogaus sprendimas), produktas **nesikuriamas**
   (generuoti SKU kirstųsi su esamais — patikrinta: prieš tai buvo 138 kolizijos).
4. Naujo kodo schema: `{supplier_prefix}{reference}` arba `{supplier_prefix}{ps_id}`;
   fallback `PS-{ps_id}`. Prefiksas per gamintojas→tiekėjas map'ą
   (`supplier_brand_prefix.csv`).
5. Gamintojai: normalized name → shop `Brand.slug`; alias'ai sulieti
   (`CCMOORE`/`CC Moore` → vienas).
6. Kategorijos: `id_category_default` → `import_code`; `1`/`2`/nemappinguota →
   `nepriskirta`. `create_missing_taxonomy` NENAUDOJAMA.
7. Kainos: `price_net = p.price` (PrestaShop DB kainos visada be PVM),
   variantams `+ pa.price` (impact). `cost_net` nėra (`wholesale_price`=0).
8. Neigiami likučiai (PrestaShop backorders) → `0`.
9. `description` → HTML→MD (be dependencijų); `meta_*` → `seo_*` laukai.
10. Nuotraukos: `{base}/img/p/{i/d/i/g/i/t/s}/{id_image}.jpg` — ingest'inamos
    shop'o pipeline, svetainė kol kas gyva.
11. `attrs` ("Grupė:Reikšmė|...") → `variants[].options` (slugified code→code),
    `variants[].features` (originalios reikšmės), `option_labels` (code→label).
12. `match_report.non_lt_descriptions` — heuristika (≥80 raidžių be LT raidžių);
    verčiama per normalyzerio OpenAI pipeline (`translate_lang`).

## Paskutiniai skaičiai (dry-run local)

- `catalog.json`: 4190 items / 5761 variants — **0 klaidų**
- `stock.json`: 1964 items — **0 klaidų**
- report: 900 pilnai matched produktų, 113 partial-match,
  ~2.4k non-LT aprašymų, ~dešimtys unikalių atributų pavadinimų

## Paleidimo seka

```powershell
# 1. Regeneruoti failus (normalyzeris)
cd normalyzer
.venv\Scripts\python.exe manage.py export_prestashop \
    [--input-dir docs_suplier/prestashop_one_time] [--base-url https://baltijoszvynas.lt]

# 2. Shop (local arba prod konteineris — komandos identiškos, jau deploy'intos)
python manage.py import_stock  <path>\stock.json              # visada pirmas
python manage.py import_catalog <path>\catalog.json --dry-run # validacija
python manage.py import_catalog <path>\catalog.json           # apply
```

Prod alternatyva be failų kopijavimo į konteinerį: `POST /api/v1/ops/imports/stock`
ir `POST /api/v1/ops/imports/catalog?apply_async=1` (staff JWT), statusas per
`GET /api/v1/ops/imports/runs/{id}`.

## Atviri darbai / rizikos

1. `match_report.review` — partial-match produktų nepriskirti vienetai (SKU gali
   priklausyti kitam shop produktui; merge'as — žmogaus sprendimas).
2. `non_lt_descriptions` (~2.4k) — versti per `translate_lang`/OpenAI po importo.
3. `attr_names` report'e — suvienodinti su esamais `Feature`/`OptionType` kodais
   (normalyzerio mapping), kad neatsirastų „Line diameter" vs „Valo diametras".
4. Katalogo apply paleis ~27k nuotraukų ingest'ą — svetainė turi būti pasiekiama.
5. `export_prestashop.py` nekomitintas normalyzerio repo (naujas failas) — pridėti.
6. **`shop_*.csv` dump'ai yra snapshot iš local dev DB.** Prieš realų PROD apply
   regeneruoti dump'us iš prod DB (prod gali turėti naujesnių prekių/brand'ų) ir
   perpaleisti konverterį — matching'as turi veikti prieš prod būseną:
   ```sql
   -- prod Postgres
   \copy (SELECT v.sku, v.barcode, v.product_id, p.sku AS product_sku
          FROM catalog_variant v JOIN catalog_product p ON p.id=v.product_id)
        TO 'shop_variants.csv' CSV HEADER;
   \copy (SELECT id, slug, name, import_source, import_code FROM catalog_brand)
        TO 'shop_brands.csv' CSV HEADER;
   \copy (SELECT id, slug, name, parent_id, level, import_code FROM catalog_category)
        TO 'shop_categories.csv' CSV HEADER;
   ```
7. Idempotencija: tas pats failas (sha256) → tas pats `ImportRun`; upsert pagal
   `code` — pakartotinis apply saugus.
