# Mivardi integracija

Viešas XML feed'as (Heureka-stiliaus) — autentikacija nebūtina.

## Architektūra

- `apps/suppliers/adapters/mivardi.py` – `mivardi` adapteris:
  `GET https://www.mivardi.com/xml/mivardivip` (3.9 MB, 2276 SKU).
  Čekiški `PARAM` vardai mapuojami į esamas BE ašis
  (`param_to_axis` config) — naujų facet'ų nekuriama.
- `apps/mapping/management/commands/map_mivardi_categories.py` –
  102 `CATEGORYTEXT` → shop kategorija + offer'iu remap.

## Semantika

- `PRODUCTNO` → external_id; `EAN` → ean; `IMAGES` → `https://www.mivardi.com/` + path.
- `CATEGORYTEXT` `Kat | Sub` → category_path `Kat > Sub`.
- `STOCK=1` & `STOCK_QUANTITY="> 50"` → stock `stock_cap` (51);
  `STOCK=1` & skaičius → tas skaičius; `STOCK=0` → 0 (flag'as autoritetas —
  45 prekės turi liekamą qty, bet neparduodamos).
- `PRICE_VAT` = RRP su PVM → `rrp_price`; **savikainos feed'e nėra**:
  `purchase_price = PRICE_VAT / (1+VAT) / 1.5` (be PVM, -50 % antkainis).
- `Průměr` < 1 → `line_diameter`, ≥ 1 → `size`; `Velikost` Navijáky→`reel_size`,
  Háčky→`hook_size`, kita→`size`; `pack`/`sections` tik skaitinėms reikšmėms.
- `MCARP` → extra.mcarp; `VIDEO_URL` → videos[tutorial].

## Env kintamieji (nebūtini – feed'as viešas, login'as yra fallback)

```
MIVARDI_USERNAME=info@baltijoszvynas.lt
MIVARDI_PASSWORD=***
```

## One-time setup (prod / cloud sesija su prod DB)

```bash
python manage.py setup_feeds             # Supplier + 2 feed'ai
python manage.py map_mivardi_categories  # 102 mapinimai + offer remap
python manage.py import_feed --supplier mivardi --feed <catalog_feed_id>
python manage.py translate_lang --prepare --language lt
python manage.py translate_lang --submit --status --apply
python manage.py export_prestashop       # kaip kitiems tiekėjams
```

## Feed'ai

| Feed | Role | Intervalas |
|---|---|---|
| Mivardi katalogas (XML) | catalog | 10080 min (savaitė) |
| Mivardi likučiai ir kainos (XML) | stock | 1440 min (diena) |

`import_due_feeds` cron paima automatiškai.

## Pastabos

- Neprekinės kategorijos (`Připravované novinky`, `Vybavení prodejen`)
  sąmoningai nemapuojamos – prekės lieka unpublished.
- Tekstai EN; kategorijos/param vardai CS — vertimo etapas sprendžia LT.
- Fixture: `docs_suplier/mivardi/feed.xml` (gitignored) — parse testams.
