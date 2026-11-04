# RidgeMonkey integracija

Tiekėjas feed'o nepateikia – katalogas scrap'inamas iš b2b.ridgemonkey.co.uk
(nopCommerce, reikia B2B login'o). Vieša svetainė ridgemonkey.co.uk yra Shopify
(katalogo palyginimui).

## Architektūra

- `apps/suppliers/adapters/ridgemonkey.py` – `ridgemonkey` adapteris:
  login per POST `/login` su `__RequestVerificationToken` → `/sitemap.xml`
  (~264 produktų puslapiai) → `product-variant-list` lentelė
  (Product | SKU | Barcode | Stock | Price) → JSON payload.
- Kainos: `19.000 (EUR) excl tax` – **be PVM** (`purchase_price_includes_vat=False`).
- Likutis: tik boolean In/Out of Stock → `config.in_stock_qty` (default 10).
- Kategorijos: breadcrumb (`Terminal Tackle > Hooks`); mapinimas –
  `map_ridgemonkey_categories.py`.
- `product_group` = puslapio slug; `external_id` = art-no (RMT286);
  shop kodas = `RM.` + art-no (`RM.RMT286`).

## Single Item vs Trade Pack (ypatumas)

Eilutės `product-variant-list` būna dviejų tipų (tame pačiame puslapyje gali
būti abiejų):

- `... - Single Item (RMC231)` – vienetinis variantas
- `... - Trade Pack of 5 (RMT286)` – pakas (5 vnt.), parduotuvėje parduodame
  **po vieną**.

Taisyklės:

- Yra Single eilučių → jos tampa variantais; `trade_pack_qty` paimtas iš
  giminės pack eilutės (to paties `base_name`).
- Tik pack eilutės → kiekviena tampa vienetiniu variantu:
  `purchase_price = pack kaina / N`, `trade_pack_qty = N`,
  `extra.unit_basis = "pack_share"` (pack EAN = dėžės barkodas).
- Rašybos klaida `Singe Item` traktuojama kaip `Single Item`.

## Sesijos apribojimas (svarbu)

**B2B leidžia vieną aktyvią sesiją** – jei kas nors prisijungia naršyklėje
tu pačiu metu, scraperio sesija nuresetinama (adapteris aptinka ir daro
relogin ≤8 kartų). Scrape'o metu **nebūti prisijungus naršyklėje** tuo pačiu
account'u.

## Env kintamieji (serverio .env / Devin Cloud secrets)

```
RIDGEMONKEY_USERNAME=info@baltijoszvynas.lt
RIDGEMONKEY_PASSWORD=***
```

## One-time setup (prod / cloud sesija su prod DB)

```bash
python manage.py setup_feeds                    # Supplier + 4 feed'ai (kredencialai iš env)
python manage.py map_ridgemonkey_categories     # CategoryMapping + offer remap
python manage.py import_feed --supplier ridgemonkey --feed <en_feed_id>
# pl/de feed'ai – kalbiniai (OfferText); grupė 'catalog' → importuojami kartu
python manage.py translate_lang --prepare --language lt
python manage.py translate_lang --submit
python manage.py translate_lang --apply
python manage.py export_prestashop              # kaip kitiems tiekėjams
```

## Feed'ai

| Feed | Role | Kalba | Intervalas | Ką daro |
|---|---|---|---|---|
| RM EN katalogas (scrape) | catalog | en | 10080 min | pilnas scrape + naujos prekės |
| RM PL katalogas (scrape) | catalog | pl | 10080 min | PL aprašymai → OfferText |
| RM DE katalogas (scrape) | catalog | de | 10080 min | DE aprašymai → OfferText |
| RM likučiai ir kainos | stock | – | 1440 min | stock/kaina/availability |

Kalbiniai feed'ai rašo tik `OfferText` (name/description) – aprašymai
verčiami tiekėjo pusėje (tikri vertimai, sutaupo MT kainų); produktų ir
variantų pavadinimai visada EN.

## Devin Cloud (online scrape)

Scrape'e ~264 puslapiai ≈ 10–15 min (`request_delay` 0.5 s). Devin Cloud
sesijoje:

1. Secrets: `RIDGEMONKEY_USERNAME` / `RIDGEMONKEY_PASSWORD` (upload-secrets).
2. `python manage.py setup_feeds` (jei dar nėra), tada `import_feed` –
   job'as registruojasi `ImportJob` (stats/error_items matomi admine).
3. Neatsidaryti b2b.ridgemonkey.co.uk naršyklėje tuo pačiu metu (vienas
   login) – adapteris relogin'uoja, bet kartodamas nutrūks.
4. Laikini `internal error` puslapiai – adapteris retry'na su backoff
   (config `retries`, `timeout`); `max_products` – testiniam apribojimui.

## Ribos / pastabos

- Eilutės EAN pack'uose = **dėžės** barkodas (vieneto retail EAN B2B neteikia).
- API importas sinchroninis – pilnam scrape naudoti manage.py/cron, ne API.
- `config.in_stock_qty` – kiekis į qty_on_hand ('In Stock' netikslus).
- Vieša Shopify svetainė – tik palyginimui; kainų/likučių joje nėra.
