# Bounty Fishing integracija

Tiekėjas failų nepateikia – visas katalogas scrap'inamas iš bounty.fishing
(OpenCart) po kliento prisijungimo.

## Architektūra

- `apps/suppliers/adapters/bounty_fishing.py` – `bounty_fishing` adapteris:
  login per POST /login/ → `/catall/?limit=600` sąrašas (515 sku) → kiekvieno
  prekės puslapis (Product Code, Availability, kaina, RPC, aprašymas,
  nuotrauka, breadcrumbs) → JSON payload.
- Pavadinimų ašys: `flavor` (uždaras žodynas), `size`, `weight`, `volume`,
  `pack`, `color` (Mix-colors). Grupavimas → `product_group` iš bendro kodų
  prefikso arba base slug. Aprašymai identiški visai linijai.
- `apps/mapping/management/commands/map_bounty_categories.py` – statinė
  source_path → shop kategorija lentelė (27 keliai) + offer'iu remap.

## Env kintamieji (serverio .env / Devin Cloud secrets)

```
BOUNTY_USERNAME=info@baltijoszvynas.lt
BOUNTY_PASSWORD=***
```

## One-time setup (prod / cloud sesija su prod DB)

```bash
python manage.py setup_feeds            # Supplier + 2 feed'ai (kredencialai iš env)
python manage.py map_bounty_categories  # 27 CategoryMapping + offer remap
python manage.py import_feed --supplier bounty_fishing --feed <catalog_feed_id>
python manage.py translate_lang --prepare --language lt
python manage.py translate_lang --submit
python manage.py translate_lang --status
python manage.py translate_lang --apply
python manage.py export_prestashop      # arba export_stock – kaip kitiems tiekėjams
```

## Feed'ai

| Feed | Role | Intervalas | Ką daro |
|---|---|---|---|
| Bounty Fishing katalogas (scrape) | catalog | 10080 min (savaitė) | pilnas scrape + naujos prekės |
| Bounty Fishing likučiai ir kainos | stock | 1440 min (diena) | tik stock/kaina/availability |

`import_due_feeds` cron paima automatiškai. ~515 detail puslapių ≈ 4–6 min
(`request_delay` 0.2 s).

## Ribos / pastabos

- API `POST /api/imports/bounty_fishing/run` yra sinchroninis – pilnas scrape
  viršija gunicorn timeout; importui naudoti manage.py / cron, ne API.
- Be login'o kainų nematyti (0.00 EUR) – kredencialai privalomi.
- EAN/barkodų svetainė neteikia – `external_id` = Product Code.
- Kainos be PVM (`purchase_price_includes_vat=False`), RPC iš spec lentelės.
- `config.flavor_vocabulary` / `filler_words` / `name_replacements` –
  naujas skonis/variacija = feed config pakeitimas be kodo deploy.
- Lokaliniai fixture'ai `docs_suplier/bounty_fishing/` (gitignored):
  `catall_scan.json` (list+code), `last_scrape.json` (pilnas detail payload –
  galima importuoti per `job.uploaded_file` be scrape).
