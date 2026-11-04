# Forgotten Flavours integracija

Tiekėjas feed'o ir B2B neturi – viešas Shopify katalogas
forgottenflavoursofficial.com (~363 produktai). Be login'o.

## Architektūra

- `apps/suppliers/adapters/forgotten_flavours.py` – `forgotten_flavours`
  adapteris: `/products.json?limit=250&page=N` (2 request'ai visam
  katalogui) → variantai → JSON payload.
- Stock feed'as papildomai per `/products/<handle>.js` paima `available`
  flag (products.json jo neturi).
- `external_id` = Shopify variant_id; `product_group` = handle;
  shop kodas = `FR.` + variant_id.
- Kategorija = `product_type` (`tags` einą į `extra.tags`) – mapinimas
  `map_forgotten_flavours_categories.py`.

## Kainos (svarbu)

- **Pirkimo kainų nėra** – `purchase_price=None`.
- Public `price` (retail, su PVM) → `offer.rrp_price`.
- `compute_selling_price`: bazė = rrp → mūsų `selling_price` per
  PriceRule taisykles (pvz. nuolaida nuo retail arba tiesiog rounding).
- Valiuta nustatoma iš `/cart.js` (Shopify Markets; iš srv2 – EUR). Jei ne EUR – konvertuojama ECB kursu; `currency` config – tik atsarginė reikšmė.

## Rate limiting

Shopify edge rate-limitina (`429 local_rate_limited`, `Retry-After`):
adapteris gerbia header'į + backoff; `request_delay` 1.0 s. Pilnas
katalogas – 2 request'ai (products.json); stock – ~363 `.js` (~7–10 min).

## Env kintamieji

Nereikia (viešas katalogas). Config – feed'ų JSON.

## Setup

```bash
python manage.py setup_feeds                          # Supplier + 2 feed'ai
python manage.py import_feed --supplier forgotten_flavours --feed <catalog_feed_id>
python manage.py map_forgotten_flavours_categories    # --dry-run: parodo source types
python manage.py translate_lang --prepare --language lt && ... --apply
python manage.py export_prestashop
```

## Feed'ai

| Feed | Role | Intervalas | Ką daro |
|---|---|---|---|
| FF katalogas (scrape) | catalog | 10080 min | products.json + naujos prekės |
| FF likučiai (scrape) | stock | 1440 min | `available` per .js → stock |

## Ribos / pastabos

- EAN/barkodai – jei variant'as turi `barcode` laukelį (ne visada).
- `is_available`/`stock` tik stock feed'e tikslūs; catalog feed'e
  laikoma `in_stock_qty` (default 10).
- MAPPINGS lentelė – po pirmo importo patikslinti pagal `--dry-run` ataskaitą.
