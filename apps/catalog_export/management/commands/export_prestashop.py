"""Vienkartinis PrestaShop dump -> shop canonical import JSON konverteris.

Skaito phpMyAdmin JSON dump'us (docs_suplier/prestashop_one_time/) ir
shop/normalyzerio CSV dump'us, sugeneruoja:

  catalog.json       - canonical catalog import (format_version=1, source="prestashop")
  stock.json         - canonical stock feed (warehouse_code="local") tik
                       prekems, kurios jau egzistuoja shop'e (matched)
  match_report.json  - audit'as: matched, review, unmapped cats/brands,
                       non-lt descriptions

Paleidimas (local):
    .venv/Scripts/python.exe manage.py export_prestashop

Po to shop pusėje:
    python manage.py import_stock stock.json
    python manage.py import_catalog catalog.json --dry-run
    python manage.py import_catalog catalog.json
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.text import slugify

BASE_URL_DEFAULT = "https://baltijoszvynas.lt"
WAREHOUSE_CODE = "local"
SOURCE = "prestashop"
FALLBACK_CATEGORY = "nepriskirta"
ROOT_CAT_IDS = {"1", "2"}
# Shop tiekėjų SKU prefiksai – matching'e nuimami, kad PS "90247" match'intų
# shop "KD.90247". Suderinama su supplier_brand_prefix.csv.
KNOWN_PREFIXES = ("FX.", "KD.", "MS.", "SH.")
# Gamintojų alias'ai (normalized -> normalized)
BRAND_ALIASES = {"ccmoore": "ccmoore", "cc moore": "ccmoore", "cc-moore": "ccmoore"}


# ---------- helpers ----------

def load_phpmyadmin_json(path: Path) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for entry in doc:
        if entry.get("type") == "table":
            rows.extend(entry.get("data") or [])
    return rows


def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def norm_name(s: str) -> str:
    """lower, be diakritikos, be skyrybos – brand/name matching'ui."""
    s = unicodedata.normalize("NFKD", (s or "").strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def norm_brand(s: str) -> str:
    s = (s or "").strip().lower()
    s = BRAND_ALIASES.get(s, s)
    s = norm_name(s)
    return BRAND_ALIASES.get(s, s)


def strip_prefix(code: str) -> str:
    c = (code or "").strip()
    for p in KNOWN_PREFIXES:
        if c.upper().startswith(p.upper()):
            return c[len(p):].strip()
    return c


def qty(v) -> int:
    try:
        return max(0, int(v or 0))
    except Exception:
        return 0


def dec(s) -> Decimal:
    try:
        return Decimal(str(s or "0")).quantize(Decimal("0.000001"))
    except Exception:
        return Decimal("0")


class _MdCleaner(HTMLParser):
    """Minimalus HTML -> Markdown be dependencių.

    Palieka: p, li, ul, ol, h1-h6, strong/b, em/i, br, a (tekstas).
    Stiliai/atributai/nuorodos išmetami.
    """

    BLOCK = {"p", "div", "section", "article", "table", "tr", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK or tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.out.append("\n\n")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "br":
            self.out.append("\n")
        elif tag in {"strong", "b"}:
            self.out.append("**")
        elif tag in {"em", "i"}:
            self.out.append("*")
        elif tag in {"h2", "h3", "h4", "h5", "h6", "h1"}:
            pass

    def handle_endtag(self, tag):
        if tag in self.BLOCK or tag.startswith("h"):
            self.out.append("\n\n")
        elif tag in {"strong", "b"}:
            self.out.append("**")
        elif tag in {"em", "i"}:
            self.out.append("*")

    def handle_data(self, data):
        self.out.append(data)

    def markdown(self) -> str:
        text = "".join(self.out)
        text = unescape(text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_md(html: str) -> str:
    if not html:
        return ""
    html = re.sub(r"(?is)<(script|style|noscript|iframe|object).*?</\1>", " ", html)
    p = _MdCleaner()
    p.feed(html)
    return p.markdown()


LT_CHARS = set("ąčęėįšųūžĄČĘĖĮŠŲŪŽ")


def looks_non_lt(text: str) -> bool:
    """Heuristika: ilgas tekstas be lietuviškų raidžių -> tikėtina EN/kita."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 80:
        return False
    return not any(c in LT_CHARS for c in letters)


def ps_image_url(base: str, image_id: str) -> str:
    path = "/".join(image_id)
    return f"{base}/img/p/{path}/{image_id}.jpg"


def opt_code(name: str) -> str:
    return slugify(name) or "attr"


# ---------- command ----------

class Command(BaseCommand):
    help = "Konvertuoja PrestaShop phpMyAdmin dump'us į shop canonical import JSON"

    def add_arguments(self, parser):
        parser.add_argument(
            "--input-dir", default="docs_suplier/prestashop_one_time",
            help="Katalogas su ps_*.json ir shop_*.csv failais",
        )
        parser.add_argument("--out-dir", default="", help="Kur rašyti output'us (default = input-dir)")
        parser.add_argument("--base-url", default=BASE_URL_DEFAULT)
        parser.add_argument("--warehouse", default=WAREHOUSE_CODE)

    def handle(self, *args, **opts):
        in_dir = Path(opts["input_dir"])
        out_dir = Path(opts["out_dir"] or opts["input_dir"])
        base_url = opts["base_url"].rstrip("/")
        wh = opts["warehouse"]

        # ---- load dumps ----
        products = load_phpmyadmin_json(in_dir / "ps_product.json")
        attrs_rows = load_phpmyadmin_json(in_dir / "ps_product_attribute.json")
        stock_rows = load_phpmyadmin_json(in_dir / "ps_stock_available_without_combination.json")
        images_rows = load_phpmyadmin_json(in_dir / "ps_image.json")
        manufacturers = load_phpmyadmin_json(in_dir / "ps_manufacturer.json")

        shop_variants = load_csv(in_dir / "shop_variants.csv")
        shop_brands = load_csv(in_dir / "shop_brands.csv")
        shop_categories = load_csv(in_dir / "shop_categories.csv")
        supplier_map = load_csv(in_dir / "supplier_brand_prefix.csv")

        # ---- indexes ----
        sku_by_exact = {r["sku"].strip(): r for r in shop_variants}
        sku_by_core = {strip_prefix(r["sku"]).upper(): r["sku"] for r in shop_variants}

        def find_sku(code: str) -> str | None:
            c = (code or "").strip()
            if not c:
                return None
            if c in sku_by_exact:
                return c
            core = c.upper()
            return sku_by_core.get(core)

        brand_slug_by_norm = {norm_brand(r["name"]): r["slug"] for r in shop_brands}
        man_name = {m["id_manufacturer"]: m["name"] for m in manufacturers}

        cat_slug_by_psid = {
            r["import_code"]: r["slug"] for r in shop_categories if r["import_code"]
        }

        brand_prefix = {
            norm_brand(r["brand_name"]): (r["supplier_code"], r["sku_prefix"])
            for r in supplier_map
        }

        attrs_by_product: dict[str, list[dict]] = {}
        for a in attrs_rows:
            attrs_by_product.setdefault(a["id_product"], []).append(a)

        stock_by_product = {s["id_product"]: qty(s.get("quantity")) for s in stock_rows}

        images_by_product: dict[str, list[dict]] = {}
        for img in images_rows:
            images_by_product.setdefault(img["id_product"], []).append(img)
        for lst in images_by_product.values():
            lst.sort(key=lambda i: int(i.get("position") or 0))

        # ---- convert ----
        catalog_items: list[dict] = []
        stock_items: list[dict] = []
        report = {
            "matched_products": [], "matched_variants": 0,
            "new_products": 0, "new_variants": 0,
            "review": [], "unmapped_categories": {}, "missing_brands": {},
            "non_lt_descriptions": [], "attr_names": {},
            "partial_match_products": [],
        }

        def cat_for(p: dict) -> str:
            cid = (p.get("id_category_default") or "").strip()
            if cid in ROOT_CAT_IDS or not cid:
                return FALLBACK_CATEGORY
            slug = cat_slug_by_psid.get(cid)
            if slug:
                return slug
            report["unmapped_categories"].setdefault(cid, 0)
            report["unmapped_categories"][cid] += 1
            return FALLBACK_CATEGORY

        def brand_for(p: dict) -> str:
            name = man_name.get(p.get("id_manufacturer") or "", "")
            slug = brand_slug_by_norm.get(norm_brand(name))
            if not slug and name:
                report["missing_brands"].setdefault(name, 0)
                report["missing_brands"][name] += 1
            return slug or ""

        def prefix_for(brand_slug_name: str) -> str:
            _, prefix = brand_prefix.get(norm_brand(brand_slug_name), ("", ""))
            return prefix or ""

        for p in products:
            pid = p["id_product"]
            prod_ref = (p.get("reference") or "").strip()
            combos = attrs_by_product.get(pid, [])
            man = man_name.get(p.get("id_manufacturer") or "", "")
            brand_slug = brand_for(p)
            prefix = prefix_for(man)

            # --- matching ---
            matched_skus: dict[str, str] = {}   # ps code -> shop sku
            if prod_ref:
                s = find_sku(prod_ref)
                if s:
                    matched_skus[prod_ref] = s
            combo_matched = 0
            for a in combos:
                aref = (a.get("reference") or "").strip()
                if aref:
                    s = find_sku(aref)
                    if s:
                        matched_skus[aref] = s
                        combo_matched += 1

            fully_matched = (
                (combos and combo_matched == len(combos))
                or (not combos and prod_ref in matched_skus)
            )
            if fully_matched:
                # visa prekė atpažinta -> tik likučiai
                report["matched_products"].append({"ps_id": pid, "skus": list(matched_skus.values())})
                if combos:
                    for a in combos:
                        aref = (a.get("reference") or "").strip()
                        sku = matched_skus.get(aref) or matched_skus.get(prod_ref)
                        if sku:
                            stock_items.append({"code": sku, "qty": qty(a.get("quantity"))})
                            report["matched_variants"] += 1
                else:
                    stock_items.append({"code": matched_skus[prod_ref], "qty": stock_by_product.get(pid, 0)})
                    report["matched_variants"] += 1
                continue
            if matched_skus:
                # dalinis atitikimas -> nekuriame naujo produkto (kitu atveju
                # generuoti SKU kirstųsi su jau esamais), tik likučiai
                # atpažintiems vienetams + review sąrašas.
                report["partial_match_products"].append({
                    "ps_id": pid, "name": p.get("name"), "matched": matched_skus,
                })
                if combos:
                    for a in combos:
                        aref = (a.get("reference") or "").strip()
                        sku = matched_skus.get(aref)
                        if sku:
                            stock_items.append({"code": sku, "qty": qty(a.get("quantity"))})
                            report["matched_variants"] += 1
                        else:
                            report["review"].append({
                                "ps_id": pid, "ps_attr_id": a.get("id_product_attribute"),
                                "ref": aref, "name": p.get("name"), "attrs": a.get("attrs"),
                            })
                else:
                    stock_items.append({"code": matched_skus[prod_ref], "qty": stock_by_product.get(pid, 0)})
                    report["matched_variants"] += 1
                continue

            # --- new product ---
            if prod_ref:
                code = f"{prefix}{prod_ref}"
            else:
                code = f"{prefix}{pid}" if prefix else f"PS-{pid}"

            desc_md = html_to_md(p.get("description") or "")
            short_md = html_to_md(p.get("description_short") or "")
            description = desc_md or short_md
            if looks_non_lt(description):
                report["non_lt_descriptions"].append({"ps_id": pid, "name": p.get("name")})

            images = [
                {"url": ps_image_url(base_url, i["id_image"]), "alt": p.get("name", ""),
                 "sort_order": int(i.get("position") or 0)}
                for i in images_by_product.get(pid, [])
            ]

            item: dict = {
                "_ps_id": pid,
                "code": code,
                "ean": (p.get("ean13") or "").strip() or None,
                "brand_code": brand_slug,
                "category": cat_for(p),
                "tax_class": "standard",
                "is_active": True,
                "price_net": str(dec(p.get("price"))),
                "weight_g": int(dec(p.get("weight")) * 1000),
                "translations": {
                    "lt": {
                        "name": (p.get("name") or "").strip(),
                        "slug": (p.get("link_rewrite") or "").strip() or slugify(p.get("name") or ""),
                        "description": description,
                        "seo_title": (p.get("meta_title") or "").strip(),
                        "seo_description": (p.get("meta_description") or "").strip(),
                    }
                },
                "images": images,
            }
            if item["ean"] is None:
                item.pop("ean")

            if combos:
                variants = []
                option_labels: dict[str, dict[str, str]] = {}
                for a in combos:
                    aref = (a.get("reference") or "").strip()
                    vcode = f"{prefix}{aref}" if aref else f"{prefix}{pid}-{a['id_product_attribute']}"
                    options: dict[str, str] = {}
                    vfeatures = []
                    for pair in (a.get("attrs") or "").split("|"):
                        if ":" not in pair:
                            continue
                        g, v = pair.split(":", 1)
                        g, v = g.strip(), v.strip()
                        if not g or not v:
                            continue
                        report["attr_names"].setdefault(g, set())
                        report["attr_names"][g].add(v)
                        gc, vc = opt_code(g), slugify(v) or v.lower()
                        options[gc] = vc
                        option_labels.setdefault(gc, {})[vc] = v
                        vfeatures.append({"code": gc, "value": v})
                    variants.append({
                        "_ps_attr_id": a["id_product_attribute"],
                        "code": vcode,
                        "supplier_code": aref,
                        "options": options,
                        "features": vfeatures,
                        "price_net": str(dec(p.get("price")) + dec(a.get("price"))),
                        "stock": {wh: qty(a.get("quantity"))},
                    })
                    report["new_variants"] += 1
                item["variants"] = variants
                item["option_labels"] = option_labels
            else:
                item["stock"] = {wh: stock_by_product.get(pid, 0)}

            catalog_items.append(item)
            report["new_products"] += 1

        # PrestaShop'e skirtingos prekės (pvz. CCMoore skoniai) dalijasi tuo pačiu
        # reference -> tas pats code. Upsert pagal code jas suliektų į vieną,
        # todėl dubliuotiems kodams pridedamas "-{ps_id}" (variantams
        # "-{id_product_attribute}"), deterministiškai.
        prod_codes = Counter(i["code"] for i in catalog_items)
        sku_codes = Counter(
            [v["code"] for i in catalog_items for v in i.get("variants", [])]
            + [i["code"] for i in catalog_items if not i.get("variants")]
        )
        report["code_collisions"] = []
        for i in catalog_items:
            pid = i.pop("_ps_id")
            if prod_codes[i["code"]] > 1 or (not i.get("variants") and sku_codes[i["code"]] > 1):
                report["code_collisions"].append({"ps_id": pid, "code": i["code"], "new_code": f"{i['code']}-{pid}"})
                i["code"] = f"{i['code']}-{pid}"
            for v in i.get("variants", []):
                attr_id = v.pop("_ps_attr_id")
                if sku_codes[v["code"]] > 1:
                    v["code"] = f"{v['code']}-{attr_id}"

        # attr_names set -> sorted list
        report["attr_names"] = {k: sorted(v) for k, v in report["attr_names"].items()}

        catalog_doc = {
            "format_version": 1,
            "source": SOURCE,
            "mode": "upsert",
            "items": catalog_items,
        }
        # Ta pati prekė PrestaShop'e gali būti įkelta kelis kartus (sena kortelė +
        # tiekėjo importas) ir abi atitinka tą patį shop SKU -> imamas max likutis.
        stock_by_code: dict[str, int] = {}
        for s in stock_items:
            stock_by_code[s["code"]] = max(stock_by_code.get(s["code"], 0), s["qty"])
        stock_doc = {
            "format_version": 1,
            "warehouse_code": wh,
            "items": [{"code": c, "qty": q} for c, q in stock_by_code.items()],
        }

        (out_dir / "catalog.json").write_text(
            json.dumps(catalog_doc, ensure_ascii=False, indent=1), encoding="utf-8")
        (out_dir / "stock.json").write_text(
            json.dumps(stock_doc, ensure_ascii=False, indent=1), encoding="utf-8")
        (out_dir / "match_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1, default=list), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(
            f"done: {report['new_products']} new products ({report['new_variants']} variants), "
            f"{len(report['matched_products'])} matched products "
            f"({report['matched_variants']} stock items), "
            f"{len(report['partial_match_products'])} partial-match, "
            f"{len(report['unmapped_categories'])} unmapped cats, "
            f"{len(report['missing_brands'])} missing brands, "
            f"{len(report['non_lt_descriptions'])} non-lt descriptions"
        ))
