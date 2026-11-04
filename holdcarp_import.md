# HoldCarp integracija

Tiekėjas feed'o ir B2B neturi – viešas PrestaShop katalogas holdcarp.sk/gb.
Be login'o. EN kalba (`/gb/`; yra ir `/sk/`).

## Architektūra

- `apps/suppliers/adapters/holdcarp.py` – `holdcarp` adapteris:
  kategorijų puslapiai (`?resultsPerPage=9999999`) → variantų nuorodos
  `/gb/{cat}/{id_product}-{id_product_attribute}-slug-{ean}.html#/attrs`
  → per produktą `data-product` JSON → per kombinaciją POST
  `?action=refresh&ajax=1&id_product_attribute=N` (`product_details` JSON:
  reference, attributes, quantity, price) → JSON payload.
- `external_id` = kombinacijos `reference` (SKU, pvz. 50695022);
  `product_group` = id_product; shop kodas = `HC.` + reference.
- EAN iš URL slug (8588…); atributai `Colour`→color, `Size`→size,
  `Dimension`/`Paper Type`→variantas.
- Kategorija = breadcrumb root (`Markers`, `Camping`, ...);
  mapinimas `map_holdcarp_categories.py`.

## Kainos

- Pirkimo kainų nėra – `purchase_price=None`; public retail EUR
  → `offer.rrp_price`; selling iš PriceRule taisyklių.
- EUR (Slovakija); `selling_price_includes_vat=True`.

## Likučiai

- `quantity` gali būti neigiamas (backorders, pvz. -42) → stock=0.
- `schema.org/PreOrder` → `extra.stock_text=preorder`, stock=in_stock_qty,
  is_available=True (užsakoma pagal užsakymą).
- Kitaip stock=quantity, is_available pagal >0.

## Env kintamieji

Nereikia (viešas katalogas). Config – feed'ų JSON.

## Setup

```bash
python manage.py setup_feeds                    # Supplier + 2 feed'ai
python manage.py import_feed --supplier holdcarp --feed <catalog_feed_id>
python manage.py map_holdcarp_categories        # --dry-run ataskaita
python manage.py translate_lang --prepare --language lt && ... --apply
python manage.py export_prestashop
```

## Feed'ai

| Feed | Role | Intervalas | Ką daro |
|---|---|---|---|
| HC katalogas (scrape) | catalog | 10080 min | kategorijų walk + prekės |
| HC likučiai (scrape) | stock | 1440 min | quantity/availability |

## Ribos / pastabos

- `skip_category_ids` (default 2,18,22) – home (`uvodna-stranka`) ir promo
  (`new-2025/2026`) kategorijos; produktai vis tiek ateina per tikras kat.
- Tas pats produktas keliose kategorijose → dedupe pagal
  (id_product, id_product_attribute); pirma kategorija laimi.
- ~150–300 produktų; request_delay 0.4 s → katalogas ~5–10 min.
