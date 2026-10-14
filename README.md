# Normalyzer

Normalyzer yra Django projektas, skirtas importuoti ir normalizuoti skirtingų tiekėjų produktų duomenis į vieningą katalogą, kuris vėliau gali būti atiduodamas per API arba eksportą e-commerce sistemai.

Projektas pradėtas siekiant vienu metu palaikyti įvairius tiekėjų duomenų formatus:

- Excel failai rankiniam įkėlimui,
- XML iš URL,
- CSV ir JSON iš URL,
- HTML duomenys, ištraukiami pagal CSS klases ar ID,
- tiekėjo API,
- custom scraper ar kitas neįprastas šaltinis.

Pagrindinė idėja – surinkti viską į vieną platformą, išsaugoti žalius duomenis diagnostikai, identifikuoti naujas ar pasikeitusias prekes ir pateikti tik patvirtintus pasiūlymus į publikuojamą katalogą.

## Pagrindiniai projektiniai tikslai

1. Gauti tiekėjo feed'ą skirtingomis priemonėmis.
2. Normalizuoti duomenis į vieningą vidinį produktų modelį.
3. Nustatyti naujas ir pasikeitusią produkciją automatiškai.
4. Susieti tiekėjo kategorijas ir prekės ženklus su vidiniu katalogu.
5. Taikyti kainų taisykles ir paskaičiuoti pardavimo kainą.
6. Atiduoti duomenis per API ir ateityje per XML/CSV eksportą.
7. Paruošti sistemą daugiakalbystei ir AI funkcijoms.

## Verslo ir architektūros koncepcija

Projekte numatomi šie etapai:

- 1 etapas: importas ir normalizacija (dabartinis branduolys)
- 2 etapas: kategorijų ir žymenų mapinimas
- 3 etapas: kainų logika ir publikuojamas katalogas
- 4 etapas: daugiakalbystė ir vertimai
- 5 etapas: AI aprašymų ir konteksto generavimas

Naujas tiekėjas turi būti įvedamas be pagrindinio kodo perrašymo. Kiekvienas tiekėjas aprašomas kaip atskiras `Supplier` ir `SupplierFeed`, o duomenų apdorojimas vyksta per adapterio sluoksnį.

## Repo struktūra

```text
config/                 Django projektas, nustatymai ir URL
apps/
  catalog/              Kanoninės prekės, kategorijos, ženklai, ProductText
  corecfg/              Language, ConfigEntry (kalbos + shop konfigūracija)
  suppliers/            Tiekėjai, feed'ai, adapterių registras
    adapters/           FOX/CCM/MOSS + generic XML/JSON/Excel adapteriai
  ingestion/            ImportJob, RawItem, Offer, OfferText, import pipeline
  mapping/              Kategorijų/ženklo/produkto susiejimai (ProductLink)
  pricing/              Kainų taisyklės ir skaičiavimas
  catalog_export/       Canonical JSON v1 eksportas, ExportRun, ShopClient
  api/                  Django Ninja routeriai ir schemas
media/                  įkeltų failų saugykla
docs_suplier/           pavyzdiniai tiekėjų duomenys (FOX/CCM Excel ir kt.)
```

## Dabartinė projektinė būklė

Šiuo metu projektas jau turi branduolį, leidžiantį:

- apibrėžti tiekėją ir feed'ą,
- importuoti duomenis per admin arba CLI,
- normalizuoti į `Offer` ir `Product` modelius,
- saugoti žalias (raw) eilutes diagnostikai,
- publikuoti tik validžius pasiūlymus,
- palaikyti tiekėjo po tiekėjo integracijas be pagrindinio kodo pakeitimų.

Dar neįgyvendinti visi AI moduliai, bet architektūra leidžia juos įlenkti vėliau be didelio refactoringo.

## Reikalavimai

- Python 3.12+
- virtualenv (`.venv`)
- pip / requirements
- PostgreSQL (lokaliai ir produkcijai)

## Pirmas paleidimas

```powershell
cd c:\Pip\baltijos_zvynas\normalyzer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env

# PostgreSQL paruošimas lokaliai:
# 1) sukurti DB: bz_normalyzer
# 2) sukurti naudotoją, pvz. standartinis-postgres
# 3) suteikti teisę prie DB ir įrašyti tuos parametrus į .env

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Prieiga:

- Admin: http://127.0.0.1:8000/admin/
- API: http://127.0.0.1:8000/api/
- OpenAPI docs: http://127.0.0.1:8000/api/docs

API autentifikacija:

- Antraštė: `X-API-Key`
- Reikšmė: `DJANGO_API_KEY` iš `.env`

## Konfigūracija

Projektas naudoja `.env` aplinkos kintamuosius. Pavyzdys:

```env
DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_API_KEY=dev-api-key

POSTGRES_DB=bz_normalyzer
POSTGRES_USER=standartinis-postgres
POSTGRES_PASSWORD=
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
CSRF_TRUSTED_ORIGINS=http://localhost:8000,https://localhost

# Shop (katalogo gavėjas) push integracija
# Auth: JWT per cookie – reikia staff vartotojo shop'e
SHOP_API_BASE_URL=
SHOP_USER_EMAIL=
SHOP_USER_PASSWORD=

# OpenAI Batch API – LT vertimai (lėtas, pigus; 24h langas)
OPENAI_API_KEY=
```

Prieš paleidžiant reikia nukopijuoti `.env.example` į `.env`.

Pastaba: jei PostgreSQL naudotojas sukurtas kitu vardu, pakeiskite `POSTGRES_USER` reikšmę `.env` faile. Produkcijoje subdomenei/HTTPS režimui įrašykite faktinius domenus į `DJANGO_ALLOWED_HOSTS` ir `CSRF_TRUSTED_ORIGINS`.

## VPS / subdomeno deploy instrukcija

Projektas turi būti vystomas taip, kad vėliau būtų pasiekiamas per VPS ir subdomenu, pvz. `api.normalyzer.lt` arba `normalyzer.example.com`.

Rekomenduojama:

- naudoti PostgreSQL ne tik lokaliai, bet ir produkcijoje;
- nustatyti `DJANGO_ALLOWED_HOSTS` į domeną arba subdomeną;
- DOMENUI per HTTPS nustatyti `CSRF_TRUSTED_ORIGINS` ir `SECURE_PROXY_SSL_HEADER` (projektas tai automatiškai palaiko, kai `DEBUG=False`);
- po reverse proxy arba Nginx/Traefik naudoti `X-Forwarded-Proto: https`;
- nepriklausomai nuo docker ar VPS, laikyti visas slaptas reikšmes `.env` ar secrete environment;
- neįtraukti `.env` į repo su konfidencialiais duomenimis.

Šis projektas turi būti paruoštas, kad galėtų veikti tiek lokaliai, tiek su HTTPS subdomenu be kodinio refactoringo.

## Darbo seka kūrėjui

### 1. Kategorijų struktūra ir mapinimas

Sistema saugo du atskirus sluoksnius:

1. **Tiekėjo kategorijas** (`category_raw`, `sub_category_raw`) – importuojamos visada iš tiekėjo feed'o.
2. **Parduotuvės kategorijas** – vidinis katalogo medis (`Category`), prie kurio tiekėjo kategorijos mapinamos rankiniu būdu.

Masiniam mapinimui naudojama atskira lentelė `CategoryMapping`, kur:

- `source_path` saugo tiekėjo kelią formatu `category > subcategory` (kai subcategory yra),
- `category` nurodo parduotuvės (e-commerce) kategoriją.
- `Offer.category_raw` ir `Offer.sub_category_raw` visada saugo originalias tiekėjo reikšmes (be perrašymo).

Jei importo metu randama nauja tiekėjo kategorija/subkategorija, `CategoryMapping` įrašas sukuriamas automatiškai net ir be priskirtos parduotuvės kategorijos.

Produkto lygmenyje rankinis priskyrimas leidžia:

- nustatyti vieną **pagrindinę** parduotuvės kategoriją (`Product.category`), kuri naudojama kaip default.

### 2. Sukurti tiekėją ir feed'ą

`Supplier` modelis turi stabilų unikalų `code`, pavyzdžiui:

- `fox`
- `supplier_a`
- `vendor_123`

`SupplierFeed` aprašo šaltinio tipą:

- `xml_url`
- `json_url`
- `csv_url`
- `excel_upload`
- `csv_upload`
- `scraper`
- `api`
- `custom`

Kiekvienas feed'as turi:

- `supplier` sąsają su tiekėju,
- `name`,
- `kind` (`xml_url`, `json_url`, `csv_url`, `excel_url`, `excel_upload`, `csv_upload`, `scraper`, `api`, `custom`),
- `role` – `catalog` (pilni duomenys), `stock` (tik likučiai/prieinamumas), `discontinued` (nutrauktos),
- `language` – feed'o tekstų kalba. Bazinės kalbos feed'as rašo `Offer` laukus; kitų kalbų feed'ai rašo `OfferText` vertimus ir neperrašo bazinių laukų,
- `feed_group` – vienos grupės feed'ai (visos kalbos) visada importuojami kartu, bazinė kalba pirmiausia,
- `sync_interval_minutes` – automatinio importo periodas (pvz. katalogas kasdien = 1440, likučiai kas valandą = 60); tuščia = tik rankinis,
- `url` arba `file`,
- `adapter_code` (jei adapteris yra konkretus),
- `config` JSON lauką (XPath, item paths, field mapping, sheet name, header row ir pan.).

Tiekėjas (`Supplier`) turi kainų semantiką:

- `vat_rate` (default 0.21),
- `selling_price_includes_vat` (default true – pardavimo kaina su PVM, eksportui skaičiuojamas `price_net`),
- `purchase_price_includes_vat` (moss = true, nes `PRICE_VAT`; fox/ccm = false),
- `export_warehouse_code` – shop sandėlio kodas likučių eksportui (tuščia = supplier.code).

Kalbos ir bendri nustatymai – `apps/corecfg` (`Language`, `ConfigEntry`).
Pradinis užpildymas (en/lt/pl/de + fox/ccm/moss feed'ai): `python manage.py setup_feeds`.

### 3. Importuoti feed'ą

CLI:

```powershell
python manage.py import_feed --supplier fox
```

Komanda importuoja **visus aktyvius tiekėjo feed'us** (kalbiniai feed'ai grupuojami – bazinė kalba pirmiausia).
Jei reikia importuoti vieną grupę:

```powershell
python manage.py import_feed --supplier fox --feed 12
```

`--feed` visada importuoja **visą grupę** – kalbiniai feed'ai niekada neimportuojami pavieniui.

Periodinis importas (cron / Windows Task Scheduler, pvz. kas 15 min.):

```powershell
python manage.py import_due_feeds
```

Importuoja visus feed'us, kuriems praėjo `sync_interval_minutes` (jei grupėje bent vienas feed'as due – importuojama visa grupė). `--dry-run` parodo sąrašą nieko neimportavus.

Arba per admin – sukurti `ImportJob` ir jį paleisti rankiniu būdu.

### 4. Patikrinti rezultatą

- `RawItem` saugo neapdorotą eilutę diagnostikai,
- `Offer` saugo normalizuotą pasiūlymą,
- `Product` naudojamas kanoniniam produktui,
- `is_published` nurodo, ar objektas yra publikuojamas į viešą API.

Publikavimo sąlyga: `category` sumap'inta **ir** `selling_price` egzistuoja **ir** `images` nėra tuščias. Prekės be nuotraukų importuojamos, bet lieka nepublikuotos (neeksportuojamos į shop).

### 5. Kainų taisyklės

Kai feed'as įkeltas, reikia suskaičiuoti kainą:

```powershell
python manage.py apply_pricing
```

Kainų variklis veikia pagal `priority` ir taiko taisykles paeiliui:

- `markup_percent`
- `markup_fixed`
- `set_price`
- `round`
- `end_99` / `end_95` / `cent`

Semantika:

- `purchase_price` saugomas **neto** (be PVM). Jei `supplier.purchase_price_includes_vat` – importe dalinama iš `1 + vat_rate` (moss `PRICE_VAT`).
- `selling_price` skaičiuojamas iš `purchase_price` per `PriceRule` taisykles; `rrp_price` yra tik informacinė rekomenduota kaina – į pardavimo kainą nekeliauja.
- Eksportui `price_net = selling_price / (1 + vat_rate)` kai `selling_price_includes_vat` (shop formatas visada be PVM).

## Adapterio kontraktas

Kiekvienas adapteris turi turėti bendrą sąsają:

- `fetch()` – paima duomenis iš URL / failo / upload'o
- `parse(payload)` – išskaido į žalių duomenų iteratorių
- `normalize(row)` – paverčia į įprastą `NormalizedItem`-tipo struktūrą

Registracijos pavyzdys:

```python
from apps.suppliers.adapters.base import BaseAdapter
from apps.suppliers.adapters.registry import register_adapter

@register_adapter
class FooAdapter(BaseAdapter):
    code = "foo"

    def parse(self, payload: bytes):
        ...
```

Paieškos tvarka adapteriui:

1. `feed.adapter_code`
2. `supplier.code`
3. generic adapteris pagal `kind`

## Generic adapterių pavyzdžiai

### XML URL

```json
{
  "item_xpath": "//product",
  "fields": {
    "external_id": "id",
    "name": "title",
    "purchase_price": "price",
    "category_path": "category",
    "ean": "barcode",
    "stock": "qty"
  }
}
```

### JSON URL

```json
{
  "items_path": "data.items",
  "fields": {
    "external_id": "sku",
    "name": "name",
    "purchase_price": "net_price"
  }
}
```

### Excel upload

```json
{
  "sheet": "BASE",
  "header_row": 1,
  "fields": {
    "external_id": "Art-No.",
    "name": "Name",
    "brand_name_raw": "Brand",
    "purchase_price": "Trade EUR",
    "ean": "Barcode Single EAN",
    "category_raw": "Category"
  }
}
```

`fields` reikšmės gali būti Excel/CSV stulpelių antraštės arba stabilūs vidiniai pavadinimai.

## Fox ir Moss pavyzdžiai

Pirmąjį supplierį reprezentuoja failas:

- `docs_suplier/fox/GB_FOX_EU_pricelist.xlsx`

FOX importui naudojamas specialus adapteris `code="fox"`, kuris jungia kelis lapus:

- `BASE` – varianto duomenys (`Art-No.`, `Name`, `Brand`, kainos, EAN, kategorijos);
- `TEXT` – bazinės grupės ir turinio tekstai (`Product Description`, `Techn. Description`, `Combined Full Text`, `Bullet01..Bullet10`);
- `MEDIA CLOUD LINKS` – nuotraukų URL nuorodos;
- `MEDIA FILES REFERENCE` – failų pavadinimai referencijai;
- `SPECS` – techniniai ir matmenų atributai.

### FOX identifikatoriai ir pavadinimai

- `external_id` = `Art-No.` (unikalus varianto ID);
- `sku` = `Art-No.` (sąmoningai nekomponuojamas iš kelių stulpelių);
- `product_group` + `identifiers.product_group` = `Product Group` (bazinės grupės ID);
- `identifiers.barcode_single_ean` = `Barcode Single EAN`;
- `identifiers.barcode_tradepack_ean` = `Barcode Tradepack EAN`;
- `name` = varianto pavadinimas iš `BASE.Name`;
- `base_name` = bazinis pavadinimas iš `TEXT.Product Description`;
- `size_raw` = varianto dydžio reikšmė iš `SPECS.Size`;
- `description` (pagrindinis aprašymas) = `TEXT.Combined Full Text`;
- `sub_category_raw` = `BASE.Sub Category`;
- `family_raw` = `BASE.Family`.

Jei varianto `Brand` laukas tuščias, FOX adapteris taiko fallback pagal `Product Group` ir kitus lapus, todėl `brand_name_raw` nelieka tuščias.

### FOX kainos ir tekstai

- pirkimo kaina: `purchase_price` iš `BASE.Trade EUR`;
- pardavimo kaina su PVM: `rrp_price` ir `selling_price` iš `BASE.RRP EUR`;
- unifikuoti siuntimo laukai:
  - `shipping_height_cm` ← `Retail Height cm`,
  - `shipping_width_cm` ← `Retail Width cm`,
  - `shipping_depth_cm` ← `Retail Depth cm`,
  - `shipping_weight_kg` ← `Retail Weight gram / 1000`;
- atskiri vertimui svarbūs laukai:
  - `combined_full_text`,
  - `technical_description`.

### FOX nuotraukos

- `images` laukui naudojamos **tik URL** reikšmės iš `MEDIA CLOUD LINKS` (`Main`, `Studio*`, kt.);
- `main_image_url` saugoma pirma nuotraukos URL;
- `MEDIA FILES REFERENCE` lapo failų vardai yra ignoruojami, nes jie neturi būti įtraukiami į `attributes`;
- nuorodos deduplikuojamos, išlaikant eiliškumą.

### FOX/CCM kalbiniai failai (lokalizuotos antraštės)

- PL/DE ir kiti kalbiniai `*_pricelist.xlsx` failai turi **išverstas stulpelių antraštes** (`Nr art.`, `Art.-Nr.`, `Grupa produktów`, ...);
- adapteris pagal `feed.language` pritaiko `FOX_HEADER_ALIASES` žemėlapį ir visas antraštes perveda į kanonines EN reikšmes, todėl visa kita logika lieka ta pati;
- papildomas/koreguojamas mapping'as galimas per `feed.config["header_map"]` (lokalizuota → kanoninė antraštė).

### MOSS kalbiniai feed'ai (kitokia XML schema)

- MOSS PL/DE XML naudoja kitokius laukų pavadinimus nei EN: `ID_PRODUCT` (=`OC`), `SKUPINA` (=`VARIANT`), `PACKAGING` (=`BALENIE`), `CENA_S_DPH_EU` (=`PRICE_VAT`), `SKLADOVOST` (=`AVAILABLE`), `SORTIMENT` (=`CATEGORYTEXT`, skirtukas ` | ` vietoj `|||`), `IMGURL_*` (=`IMG*`), `VIDEO_*` (=`YOUTUBE*`);
- adapteris `_item_to_row` stadijoje perveda juos į kanoninius EN pavadinimus – visa kita logika lieka ta pati.

### MOSS video

- XML laukuose `YOUTUBE`, `YOUTUBE_2`, ... tiekėjas atiduoda **tik YouTube video ID**;
- adapteris jį paverčia pilnu `https://youtu.be/<id>` URL (vienodas formatas kaip FOX) ir deda į `videos` su `type="product"`, `language=""` (video nekinta per kalbas).

### FOX video

- `VIDEOS` lapo `Product Video (XX)`, `Instructional Video (XX)`, `Tutorial Video (XX)` stulpeliai surenkami į `Offer.videos` kaip `[{url, type, language}]` (type: `product|instructional|tutorial`, language iš stulpelio kodo, pvz. `en`/`pl`/`de`);
- kalbiniai feed'ai (pvz., PL) merge'ina savo kalbos video į tą patį offer'į (dedup pagal URL);
- produkto lygmenyje `Product.videos` yra visų offer'ų video sujungimas;
- eksportui `videos` blokas eina į `catalog_import` payload'ą; shop'e išsaugoma `ProductVideo` modeliuose (puslapyje: `product.videos.all`).

### FOX specifikacijos ir pristatymo matmenys

- nuo `SPECS` lapo stulpelio `Unit Height` ir toliau reikšmės saugomos `attributes.specs`;
- e-komercijai reikalingi pristatymo matmenys išskiriami į `attributes.shipping_dimensions` ir paliekami tik su `Retail ...` lauko pavadinimais;
- `attributes` lauke lieka tik prekybiniai filtrai ir techninės savybės, ne media failų pavadinimai.

### MOSS XML pavyzdys

MOSS importui naudojamas specialus adapteris `code="moss"` (šaltinis `moss-feed-en.xml`), kuris:

- ima `external_id` ir `sku` iš `OC`;
- naudoja `VARIANT` kaip bazinės prekės nuorodą (jei tuščias, įrašas laikomas default variantu);
- suvienodina kategoriją iš `CATEGORYTEXT`, pakeisdamas `|||` į ` > `, pvz.  
  `Fishing lines ||| Braided lines - main lines ||| Spinning` →  
  `Fishing lines > Braided lines - main lines > Spinning`;
- normalizuoja kategoriją į:
  - `category_raw` = pirmas lygis (`Fishing lines`),
  - `sub_category_raw` = likęs kelias (`Braided lines - main lines > Spinning`);
- surenka visas nuotraukas iš `IMG`, `IMG_2`, `IMG_3` ... su deduplikacija;
- kainą ima iš `PRICE_VAT` (EUR), kiekį iš `AVAILABLE`;
- `extra.product_group` nustato į bazinės prekės `OC`, todėl default įrašas ir jo variantai susiejami su tuo pačiu `Product`;
- `attributes` prideda `variant_value` (`ROZMER`), `weight_kg` (konvertuojama iš `WEIGHT` gramų į kg) ir `dimensions_cm` (`WIDTH`, `LENGTH`, `HEIGHT`).
- `ROZMER`, `BALENIE`, `HMOTNOST` papildomai saugomi normaliuose laukuose:
  - `Offer.size_raw` / `Product.size_raw`,
  - `Offer.package_raw` / `Product.package_raw`,
  - `Offer.unit_weight_raw` / `Product.unit_weight_raw`.
- unifikuoti siuntimo laukai:
  - `shipping_height_cm` ← `HEIGHT`,
  - `shipping_width_cm` ← `WIDTH`,
  - `shipping_depth_cm` ← `LENGTH`,
  - `shipping_weight_kg` ← `WEIGHT / 1000`.

## Naujo tiekėjo onboarding checklist

Pridedant naują tiekėją, reikia patikrinti:

1. Ar šaltinis yra URL, upload, HTML, API ar custom scraper.
2. Koks yra stabilus `external_id` ir ar jis skiriasi tarp įkėlimų.
3. Kaip susiejami tiekėjo stulpeliai su vidiniais laukais.
4. Kaip atvaizduojama kategorija, prekės ženklas ir EAN.
5. Ar reikia handlinimo pasikeitimų ir deduplikacijų.
6. Ar reikia papildomos kainų logikos.
7. Ar raw duomenys yra atskirti nuo normalizuotų.
8. Ar vėliau prireiks AI vertimų ar aprašymų.

## Daugiakalbystė ir AI

Katalogo ateities dalis – automatizuotas vertimas ir produkto aprašymų generavimas.

Numatomi komponentai:

- vertimai į kitas kalbas per DeepL arba OpenAI,
- AI sugeneruoti aprašymai pagal kategoriją, prekės ženklą, kontekstą ir tiekėjo charakteristikas,
- skirtingų kalbų katalogų palaikymas ir `locale` tag'ai,
- originalus tekstas paliekamas kartu su sugeneruotais variantais.

Svarbu: vertimai ir aprašymai turi būti atskirti nuo bazinių produktų duomenų. Tiekėjo feed'ai turi likti originalūs, o AI rezultatai – papildomas, opt-in procesas.

## API (santrauka)

| Metodas | Kelias | Aprašymas |
| --- | --- | --- |
| GET | `/api/offers/` | Publikuoti pasiūlymai |
| GET | `/api/products/` | Kanoninės prekės |
| GET | `/api/categories/` | Vidinės kategorijos |
| GET | `/api/suppliers/` | Tiekėjai |
| POST | `/api/imports/{code}/run` | Importo paleidimas (kartu importuoja visą feed grupę) |
| POST | `/api/imports/{code}/upload` | Failo įkėlimas ir importas |

## Eksportas į shop (canonical JSON v1)

Normalyzeris generuoja `catalog_import.md` aprašytą formatą (`format_version: 1`):

```powershell
# Katalogas
python manage.py export_catalog --out catalog.json          # į failą
python manage.py export_catalog --push                      # POST į shop ops API
python manage.py export_catalog --supplier fox --dry-run    # peržiūra

# Likučiai (per supplier -> warehouse_code)
python manage.py export_stock --supplier fox --push
```

Push konfigūracija `.env`: `SHOP_API_BASE_URL`, `SHOP_USER_EMAIL`, `SHOP_USER_PASSWORD` (JWT login `/api/v1/auth/login`, staff vartotojas).
Kiekvienas eksportas registruojamas `ExportRun` (sha256, item_count, shop atsakymas).

### Shop kategorijų sinchronizavimas

Shop kategorijos yra kanoninis šaltinis eksporto `category` laukui:

```powershell
python manage.py sync_shop_categories
```

Komanda paima **visą** shop kategorijų medį per `/api/v1/ops/imports/categories` ir atnaujina vietinį `Category` (slug = shop slug). Reikalingas staff vartotojas (`.env` `SHOP_USER_*`).

### LT vertimas (OpenAI Batch API)

Shop reikalauja `translations.lt`; tiekėjų LT feed'ų nėra → LT gauname AI vertimu:

```powershell
python manage.py translate_lt --prepare [--limit N]   # sugeneruoja JSONL batch failą
python manage.py translate_lt --submit                 # įkelia į OpenAI Batch API
python manage.py translate_lt --status                 # progresas
python manage.py translate_lt --apply                  # įrašo į OfferText + ProductText
```

- verčiami `name`, `base_name`, `description`, `technical_description` (`combined_full_text` praleidžiamas – jis ir yra `description` šaltinis);
- system prompt'as turi žvejybos domenu žodynėlį (rod→meškerė, reel→ritė, boilie→boilius...);
- modelis: `gpt-4.1-mini` default (`ConfigEntry translation_model` arba `--model` perrašo);
- kol LT nėra – eksportas naudoja EN fallback (`FALLBACK_TRANSLATION_LANG`).

### Video (YouTube)

- FOX/CCM: `VIDEOS` lapas `Product/Instructional/Tutorial Video (XX)` → `Offer.videos` kaip `[{url,type,language}]`;
- MOSS: `YOUTUBE*`, `VIDEO_*` → video ID → `https://youtu.be/<id>` (vienodas formatas kaip FOX);
- kalbiniai feed'ai merge'ina savo kalbos video (dedup pagal URL); `Product.videos` agreguoja visų offer'ų;
- eksportui → `videos` blokas; shop'e → `ProductVideo` modelis.

Variantai: `Product` grupuoja susietus `Offer`'ius; kiekvienas offer = variantas (`code`=offer.sku, `ean`, `options.size`, `price_net`, `stock{warehouse:qty}`). Tas pats EAN iš kelių tiekėjų = vienas variantas su kelių sandėlių likučiais.

Vertimai: `translations.en` = baziniai `Product` laukai; `pl`/`de` = `ProductText`/`OfferText` iš kalbinių feed'ų; `lt` = AI vertimas (`translate_lt`) arba EN fallback.

Puslapiavimas:

- `?page=1` (po 100 elementų)
- numatytas pagination yra naudojamas API endpointuose

## Testavimas

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Rekomenduojama testuoti:

- adapterių mappinimą,
- kategorijų normalizaciją,
- `Offer` / `Product` deduplikaciją,
- kainų taisyklių logiką,
- import pipeline iš žalių duomenų į publikuojamą variantą.

## Admin peržiūra (FOX)

`Offer` admin sąraše matomi papildomi stulpeliai:

- `Art-No.` (external_id),
- `Product Group`,
- `base_name`,
- `brand_name_raw`,
- `purchase_price`, `rrp_price`, `selling_price`,
- `sub_category_raw`, `family_raw`,
- `Images` (nuotraukų URL kiekis),
- `Main image` (miniatiūra su nuoroda).

`Product` admin sąraše matomi:

- miniatiūra (`thumbnail`),
- `source_external_id`, `product_group`,
- `base_name`, `sub_category_raw`, `family_raw`,
- `purchase_price`, `rrp_price`.

Tai leidžia greitai patikrinti, ar FOX daugialapis importas tikrai atnešė identifikatorius ir nuotraukų nuorodas.

## Kaip plėsti sistemą

### Kai reikia papildomo tiekėjo tipo

- sukurti atskirą adapterį `apps/suppliers/adapters/`
- pridėti `register_adapter` dekoratorių
- nurodyti `SupplierFeed.adapter_code` arba tiekėjo `code`
- konfigūruoti `config` JSON lauką

### Kai reikia HTML parserio

- `fetch()` metodas grąžina HTML arba JSON,
- `parse()` ieško įrašų pagal CSS selector arba XPath,
- `normalize()` paverčia į bendrą formatą

### Kai reikia naujo tiekėjo be branduolio pakeitimų

- sukurti `Supplier`
- sukurti `SupplierFeed`
- nustatyti `kind` ir `config`
- paleisti `python manage.py import_feed --supplier <code>`

## Darbotvarkė ir roadmap

### Artimiausia fazė

- papildyti realių tiekėjų adapterių onboarding
- suformuoti kategorijų mapinimo taisykles
- modelio `Product` deduplikacijos taisyklės
- XML/CSV eksportų API struktūra

### Tolesni žingsniai

- PostgreSQL migracija produkcijai
- Celery / background jobs ilgiems importams
- AI vertimų ir aprašymų modulis
- API exportai e-commerce platformoms

## Git ir dokumentacijos pastaba

Šis projektas turi būti vystomas nuolat kartu su `README.md` atnaujinimu. Kiekvienas naujas adapteris, feed tipas arba AI modulis turi būti dokumentuojamas čia, kad naujasis kūrėjas galėtų pradėti be papildomo „pasiklausimo“.

## Pagrindinė projektinė mintis

Sistema turi būti ne vien „importeris“, bet normalizavimo platforma: ji renka duomenis iš skirtingų šaltinių, išsaugo žalius duomenis, identifikuoja naujoves, susieja kategorijas ir produktus, skaičiuoja kainas, atiduoda publikuojamą katalogą ir ateityje plečiasi iki multilingual, AI-driven product operations.

Tai ilgalaikis projektas, kurio branduolys turi likti stabilus, o tiekėjai ir adapteriai – plėsti be didelių architektūros ar duomenų struktūros pakeitimų.
