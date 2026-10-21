from __future__ import annotations

import hashlib
import json
import logging
import time
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

from apps.catalog.models import Category, Product
from apps.corecfg.models import Language, get_config
from apps.ingestion.models import Offer
from apps.suppliers.models import Supplier

from .models import ExportRun

logger = logging.getLogger(__name__)

FORMAT_VERSION = 2
CATALOG_PATH = "/api/v1/ops/imports/catalog"
STOCK_PATH = "/api/v1/ops/imports/stock"
DEFAULT_TAX_CLASS = "standard"
# Kol nėra LT vertimų pipeline – lt transliacija pildoma baziniu tekstu.
FALLBACK_TRANSLATION_LANG = "lt"


def _net(value: Decimal | None, includes_vat: bool, vat_rate: Decimal) -> Decimal | None:
    if value is None:
        return None
    if not includes_vat:
        return value.quantize(Decimal("0.01"))
    return (value / (Decimal("1") + vat_rate)).quantize(Decimal("0.01"))


def _warehouse_code(supplier: Supplier) -> str:
    return supplier.export_warehouse_code or supplier.code


def _translation_block(product: Product, base_lang: str) -> dict:
    """translations{lang: {name, slug, description}} pagal format_version 1."""
    texts: dict[str, dict] = {}
    for text in product.texts.select_related("language"):
        code = text.language.code
        texts[code] = {
            "name": text.name,
            "slug": text.slug or slugify(text.name)[:220],
            "description": text.description,
            "seo_title": text.seo_title,
            "seo_description": text.seo_description,
            "seo_keywords": text.seo_keywords,
        }
    base = {
        "name": product.name,
        "slug": slugify(product.name)[:220],
        "description": product.description,
        "seo_title": product.seo_title,
        "seo_description": product.seo_description,
        "seo_keywords": product.seo_keywords,
    }
    texts.setdefault(base_lang, base)
    # Shop reikalauja lt; kol nėra vertimų pipeline – bazinis tekstas.
    texts.setdefault(FALLBACK_TRANSLATION_LANG, dict(base))
    return texts


from apps.catalog.attributes import (
    ATTRIBUTE_META,
    MAX_OPTIONS,
    VARIANT_LABEL_CODE,
    classify_label,
    is_meaningful_value,
    resolve_code,
    strip_axis_prefix,
    value_code,
    value_in_name,
)
from apps.catalog.profiles import DEFAULT_PROFILE, CategoryProfile, profile_for
from apps.catalog.unmapped import collect, report
from apps.catalog.values import parse_value


def _code(raw: str) -> str:
    """Kanoninis feature/ašies kodas: 'Unit_Length' -> 'unit-length'.

    Feeds naudoja ir '_' ir '-' separatorius tam pačiam atributui –
    normalizuojame, kad backend'e nekiltų 'ball_bearings'/'ball-bearings'
    duplikatų.
    """
    return str(raw).strip().lower().replace("_", "-")


# Kategorijų vardai (id -> (name, parent_id)) – užkraunami vieną kartą
# per build_catalog_payload, kad kontekstas neiti per parent FK DB'e
# kiekvienam offer'iui. Išvalomas eksportui pasibaigus.
_CATEGORY_NAMES: dict[int, tuple[str, int | None]] = {}


def _load_category_names() -> None:
    _CATEGORY_NAMES.update(
        {
            row[0]: (row[1] or "", row[2])
            for row in Category.objects.values_list("id", "name", "parent_id")
        }
    )


def _category_context(category_id) -> str:
    parts: list[str] = []
    seen: set[int] = set()
    node_id = category_id
    while node_id and node_id not in seen:
        seen.add(node_id)
        name, parent_id = _CATEGORY_NAMES.get(node_id, ("", None))
        parts.append(name)
        node_id = parent_id
    return " ".join(parts)


def _product_context(product) -> str:
    """Produkto lygio kontekstas kontekstinėms taisyklėms (_features)."""
    parts = [_category_context(product.category_id)]
    parts.append(product.category_raw or "")
    parts.append(product.sub_category_raw or "")
    return " ".join(parts).lower()


def _offer_context(offer) -> str:
    """Kontekstas kontekstinėms taisyklėms: kategorijos (su parent'ais)
    + žalie tiekėjo laukai, viskas lowercase."""
    category_id = offer.category_id or (
        offer.product.category_id if offer.product_id else None
    )
    parts = [_category_context(category_id)]
    parts.append(offer.category_raw or "")
    parts.append(offer.sub_category_raw or "")
    if offer.product_id:
        parts.append(offer.product.category_raw or "")
        parts.append(offer.product.sub_category_raw or "")
    return " ".join(parts).lower()


def offer_axis_values(offer) -> dict[str, str]:
    """Žali ašių kandidatai: canonical axis -> reikšmė (display).

    Šaltiniai: attributes['axes'] (kanoniniai kodai), legacy
    attributes['specs'] (lokalizuoti label'iai -> classify_label),
    MOSS size_raw -> 'size'. Kodai eina per `resolve_code` – kodas
    ne registre praleidžiamas ir fiksuojamas UnmappedAttribute.
    'variantas' nėra ašis – liekana eina į variants[].name
    (offer_variant_label).
    """
    context = _offer_context(offer)
    supplier_code = offer.supplier.code if offer.supplier_id else ""
    category_raw = offer.category_raw or ""

    axes: dict[str, str] = {}
    if offer.size_raw:
        axes["size"] = offer.size_raw.strip()
    attrs = offer.attributes or {}
    raw_axes = attrs.get("axes")
    if isinstance(raw_axes, dict):
        for code, value in raw_axes.items():
            value = strip_axis_prefix(str(value))
            if not value:
                continue
            canonical = resolve_code(str(code), context)
            if canonical is None:
                if str(code).strip().lower() == VARIANT_LABEL_CODE:
                    continue  # -> variant_label, ne atributas
                report(
                    supplier=supplier_code, code=code, label=code,
                    kind="unmapped_code", category_raw=category_raw,
                    value=value,
                )
                continue
            if ATTRIBUTE_META[canonical]["kind"] != "axis":
                continue
            axes.setdefault(_code(canonical), value)
    specs = attrs.get("specs")
    if isinstance(specs, dict):
        for label, value in specs.items():
            if str(label).strip().lower() == VARIANT_LABEL_CODE:
                continue
            code, kind = classify_label(str(label))
            if kind != "axis" or not code:
                continue
            value = strip_axis_prefix(str(value))
            if not value:
                continue
            canonical = resolve_code(code, context)
            if canonical is None:
                report(
                    supplier=supplier_code, code=code, label=label,
                    kind="unmapped_code", category_raw=category_raw,
                    value=value,
                )
                continue
            if ATTRIBUTE_META[canonical]["kind"] != "axis":
                continue
            axes.setdefault(_code(canonical), value)
    return axes


def offer_variant_label(offer) -> str:
    """'variantas' liekana -> varianto pavadinimo dalis (ne atributas)."""
    attrs = offer.attributes or {}
    raw_axes = attrs.get("axes")
    if isinstance(raw_axes, dict):
        for code, value in raw_axes.items():
            if str(code).strip().lower() == VARIANT_LABEL_CODE:
                return str(value).strip()
    specs = attrs.get("specs")
    if isinstance(specs, dict):
        for label, value in specs.items():
            if str(label).strip().lower() == VARIANT_LABEL_CODE:
                return str(value).strip()
    return ""


def _variant_meta(axis: str, value, supplier: str = "") -> dict:
    """parse_value meta vienai ašies reikšmei: code/label + num/unit."""
    meta = ATTRIBUTE_META.get(_norm_axis(axis)) or {}
    parsed = parse_value(
        value, meta.get("datatype", "text"), meta.get("unit", ""),
        axis=axis, supplier=supplier,
    )
    return {
        "code": parsed.code,
        "label": parsed.display,
        "num": parsed.num,
        "num_max": parsed.num_max,
        "unit": parsed.unit,
        "datatype": meta.get("datatype", "text"),
    }


def _norm_axis(axis: str) -> str:
    return str(axis or "").strip().lower().replace("-", "_")


def _plan_variants(
    offers: list[Offer],
    profile: "CategoryProfile | None" = None,
) -> tuple[dict[int, dict], dict[str, str], dict, dict[int, dict], dict]:
    """Grupės lygio ašių planas – deklaratyvus option rinkinys.

    - Option kandidatai = varijuojančios ašys ∩ `profile.axes`
      (per-kategorijos deklaracija, apps.catalog.profiles).
    - Greedy minimalus rinkinys išlaikomas, bet ALL-OR-NOTHING:
      jei profilio ašys visų variantų neatskiria -> options tuščios
      (pirkėjas renkasi pagal varianto pavadinimą).
    - Pasirinktos option ašys IR varijuojančios neoption ašys eina į
      variant.features (option = feature, kuris dar yra selektorius).
    - tip_diameter == line_diameter -> viena ašis.
    - Reikšmės normalizuojamos (XXL=2xl, '0,08MM'->'0-08mm', White=white).

    Grąžina ({offer_id: {axis: code}} options, {axis: label} demoted ->
    product features, {axis: {code: label}} option_labels ->
    shop OptionValue.label, {offer_id: {axis: meta}} variant_features ->
    variant.features, {axis: {code: {num,num_max,unit}}} option_value_meta.
    """
    if profile is None:
        profile = DEFAULT_PROFILE
    raw = {offer.id: offer_axis_values(offer) for offer in offers}
    suppliers = {
        offer.id: (offer.supplier.code if offer.supplier_id else "")
        for offer in offers
    }
    # Žalios reikšmės name-match'ui (prieš normalizavimą: '1.75m' name'e)
    display = {oid: dict(axes) for oid, axes in raw.items()}
    names = {offer.id: offer.name or "" for offer in offers}
    # Reikšmių normalizavimas prieš varying patikrą: 'White'/'white',
    # 'XXL'/'2XL' yra ta pati reikšmė (ne atskiras variantas).
    labels: dict[str, dict[str, str]] = {}
    option_value_meta: dict[str, dict[str, dict]] = {}
    meta_by_offer: dict[int, dict[str, dict]] = {}
    for oid, axes in raw.items():
        meta_by_offer[oid] = {}
        if axes.get("tip_diameter") and axes["tip_diameter"] == axes.get("line_diameter"):
            del axes["tip_diameter"]
        for ax in list(axes):
            meta = _variant_meta(ax, axes[ax], supplier=suppliers.get(oid, ""))
            meta_by_offer[oid][ax] = meta
            code, label = meta["code"], meta["label"]
            if not code:
                del axes[ax]
                continue
            axes[ax] = code
            labels.setdefault(ax, {}).setdefault(code, label)
            if meta["num"] is not None:
                option_value_meta.setdefault(ax, {})[code] = {
                    "num": _num_str(meta["num"]),
                    "num_max": _num_str(meta["num_max"]),
                    "unit": meta["unit"],
                }

    all_axes = {ax for axes in raw.values() for ax in axes}
    varying = {
        ax
        for ax in all_axes
        if len({axes[ax] for axes in raw.values() if ax in axes}) > 1
    }

    # Profilio ašys kanoniniais snake_case kodais; eksporto raktai kebab.
    profile_axes_kebab = [_code(ax) for ax in profile.axes]
    profile_index = {ax: i for i, ax in enumerate(profile_axes_kebab)}

    def _rank(ax: str) -> tuple:
        in_name = sum(
            1
            for oid, axes in raw.items()
            if ax in axes
            and value_in_name(ax, display[oid].get(ax), names[oid])
        )
        coverage = sum(1 for axes in raw.values() if axes.get(ax))
        return (profile_index.get(ax, 99), -in_name, -coverage)

    def _collisions(selected: list[str]) -> int:
        combos = [tuple(axes.get(ax, "") for ax in selected) for axes in raw.values()]
        return len(combos) - len(set(combos))

    # Greedy minimalus rinkinys TIK iš profilio ašių.
    candidates = sorted(set(varying) & set(profile_axes_kebab), key=_rank)
    chosen: list[str] = []
    while candidates and len(chosen) < MAX_OPTIONS and _collisions(chosen) > 0:
        best = min(
            candidates,
            key=lambda ax: (_collisions([*chosen, ax]), _rank(ax)),
        )
        candidates.remove(best)
        chosen.append(best)
    # All-or-nothing: nepilnai atskiriantys options -> options visai nebe.
    if _collisions(chosen) > 0:
        chosen = []

    # Nepanaudotos ašys:
    # - nevarijuojančios -> product features (viena reikšmė visam produktui)
    # - varijuojančios -> variant.features (kiekvienas variantas turi SAVO
    #   reikšmę). Option ašys IR GI yra variant.features (option =
    #   feature + selektorius).
    demoted: dict[str, str] = {}
    variant_features: dict[int, dict[str, dict]] = {offer.id: {} for offer in offers}
    for ax in all_axes:
        varying_ax = ax in varying
        for oid, axes in raw.items():
            if ax not in axes:
                continue
            meta = meta_by_offer[oid].get(ax) or {}
            label = meta.get("label") or labels.get(ax, {}).get(axes[ax], axes[ax]) or axes[ax]
            if varying_ax:
                variant_features[oid][ax] = meta
            else:
                # Žalia reikšmė – _features pats parsuoja num/unit.
                demoted.setdefault(ax, display[oid].get(ax, label))
    options = {
        oid: {ax: axes[ax] for ax in axes if ax in chosen}
        for oid, axes in raw.items()
    }
    return options, demoted, labels, variant_features, option_value_meta


def _num_str(value) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def _features(
    product: Product,
    demoted_axes: dict[str, str] | None = None,
    *,
    context: str = "",
    supplier: str = "",
) -> list[dict]:
    attrs = product.attributes or {}
    category_raw = product.category_raw or ""
    features: list[dict] = []
    seen: set[str] = set()

    def add(raw_code, value, raw_label: str = ""):
        resolved = resolve_code(str(raw_code), context)
        if resolved is None:
            report(
                supplier=supplier, code=raw_code,
                label=raw_label or str(raw_code),
                kind="unmapped_code", category_raw=category_raw, value=value,
            )
            return
        meta = ATTRIBUTE_META.get(resolved) or {}
        code = _code(resolved)
        value = str(value).strip()
        if not is_meaningful_value(value) or code in seen:
            return
        seen.add(code)
        parsed = parse_value(
            value, meta.get("datatype", "text"), meta.get("unit", ""),
            axis=resolved, supplier=supplier,
        )
        features.append(
            {
                "code": code,
                "value": parsed.code or value_code(value),
                "name_translations": {
                    "en": meta.get("label") or resolved.replace("_", " ").title()
                },
                "value_translations": {"en": parsed.display or value},
                "value_num": _num_str(parsed.num),
                "value_num_max": _num_str(parsed.num_max),
                "unit": parsed.unit,
                "datatype": meta.get("datatype", "text"),
            }
        )

    # Kanoniniai features (naujas adapterio formatas)
    raw_features = attrs.get("features")
    if isinstance(raw_features, dict):
        for code, value in raw_features.items():
            add(str(code), value)
    # Legacy specs: lokalizuoti label'iai -> classify
    specs = attrs.get("specs")
    if isinstance(specs, dict):
        for label, value in specs.items():
            code, kind = classify_label(str(label))
            if kind == "feature" and code:
                add(code, value, raw_label=str(label))
    # Ašys su vienoda reikšme visame produkte -> feature
    for ax, value in (demoted_axes or {}).items():
        add(ax, value)
    # Compliance: Material/Fabric Information -> feature (trumpa);
    # gpsr/ingredients -> additional_info (ilgas teisinis tekstas, ne feature)
    compliance = attrs.get("compliance")
    if isinstance(compliance, dict):
        add("material_fabric", compliance.get("material_fabric"))
    return features


def _additional_info(product: Product) -> dict[str, str]:
    """GPSR / ingredients -> Product.additional_info (ne feature – ilgas tekstas)."""
    compliance = (product.attributes or {}).get("compliance")
    if not isinstance(compliance, dict):
        return {}
    return {
        key: str(compliance[key]).strip()
        for key in ("gpsr", "ingredients")
        if str(compliance.get(key) or "").strip()
    }


def _images(product: Product) -> list[dict]:
    urls = product.images if isinstance(product.images, list) else []
    if product.main_image_url and product.main_image_url not in urls:
        urls = [product.main_image_url, *urls]
    return [
        {"url": url, "alt": product.name, "sort_order": index}
        for index, url in enumerate(urls)
        if url
    ]


def _videos(product: Product) -> list[dict]:
    """YouTube/video URL'ai: [{url, type, language}], dedup pagal url."""
    videos = product.videos if isinstance(product.videos, list) else []
    result = []
    seen: set[str] = set()
    for video in videos:
        url = str((video or {}).get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(
            {
                "url": url,
                "type": str(video.get("type") or "product"),
                "language": str(video.get("language") or ""),
            }
        )
    return result


def _offer_variant_code(offer: Offer) -> str:
    """Variant code = shop Variant.sku – unikalus globaliai.

    Skirtingi tiekėjai gali turėti sutampančius art-no (FOX 'GRD198' =
    Matrix, Korda 'GRD198' = Guru), todėl tiekėjas gali nurodyti
    sku_prefix – tikrasis art-no lieka 'supplier_code' lauke.
    """
    base = (offer.sku or "").strip() or offer.external_id
    prefix = (offer.supplier.sku_prefix or "").strip()
    return f"{prefix}{base}" if prefix else base


def _to_int_cm(value) -> int:
    if value is None:
        return 0
    try:
        return int(Decimal(str(value)).to_integral_value())
    except (InvalidOperation, ValueError):
        return 0


def _variant_keys(offer: Offer) -> set[str]:
    """Identiteto raktai: EAN ir art-no. Sutapęs bet kuris = tas pats variantas."""
    keys = {f"sku:{_offer_variant_code(offer).lower()}"}
    if offer.ean:
        keys.add(f"ean:{offer.ean.strip().lower()}")
    return keys


def _new_variant(
    offer: Offer,
    options: dict,
    variant_features: dict | None = None,
    name: str = "",
) -> dict:
    price_net = _net(
        offer.selling_price,
        offer.supplier.selling_price_includes_vat,
        offer.supplier.vat_rate,
    )
    cost_net = (
        offer.purchase_price.quantize(Decimal("0.01"))
        if offer.purchase_price is not None
        else None
    )
    # Dėžė kaip prekė: variantas = visa trade-pack pakuotė, kaina
    # = vieneto kaina × trade_pack_qty. Pack kainos skaičiuojamos iš
    # gross (selling) prieš net'iškinimą – tiksliau negu net×qty.
    pack_qty = offer.trade_pack_qty or 0
    pack_factor = Decimal(pack_qty) if pack_qty > 1 else None
    pack_price = None
    pack_purchase_price = None
    if pack_factor is not None:
        if offer.selling_price is not None:
            pack_price = _net(
                offer.selling_price * pack_factor,
                offer.supplier.selling_price_includes_vat,
                offer.supplier.vat_rate,
            )
        if offer.purchase_price is not None:
            pack_purchase_price = (offer.purchase_price * pack_factor).quantize(
                Decimal("0.01")
            )
    if offer.sold_as_pack:
        price_net = pack_price or price_net
        cost_net = pack_purchase_price or cost_net
    return {
        "code": _offer_variant_code(offer),
        "name": name,
        "supplier_code": offer.external_id,
        "product_group_code": offer.product_group or "",
        "ean": offer.ean or offer.pack_barcode or "",
        "options": options,
        "features": [
            {
                "code": ax,
                "value": m.get("code") or value_code(m.get("label") or ""),
                "name_translations": {
                    "en": (ATTRIBUTE_META.get(_norm_axis(ax)) or {}).get("label")
                    or ax.replace("-", " ").replace("_", " ").title()
                },
                "value_translations": {"en": m.get("label") or ""},
                "value_num": _num_str(m.get("num")),
                "value_num_max": _num_str(m.get("num_max")),
                "unit": m.get("unit") or "",
                "datatype": m.get("datatype") or "text",
            }
            for ax, m in (variant_features or {}).items()
            if m.get("label")
        ],
        "price_net": str(price_net) if price_net is not None else None,
        "cost_net": str(cost_net) if cost_net is not None else None,
        "pack_unit": offer.pack_unit or "",
        "pack_quantity": offer.pack_quantity or 0,
        "trade_pack_qty": pack_qty,
        "pack_barcode": offer.pack_barcode or "",
        "sold_as_pack": bool(offer.sold_as_pack),
        "pack_price": str(pack_price) if pack_price is not None else None,
        "pack_purchase_price": (
            str(pack_purchase_price) if pack_purchase_price is not None else None
        ),
        "weight_g": _to_int_cm(
            (offer.shipping_weight_kg * 1000) if offer.shipping_weight_kg else None
        ),
        "height_cm": _to_int_cm(offer.shipping_height_cm),
        "width_cm": _to_int_cm(offer.shipping_width_cm),
        "length_cm": _to_int_cm(offer.shipping_depth_cm),
        "stock": {},
        "images": [],
    }


def _add_offer_images(variant: dict, offer: Offer) -> None:
    for url in offer.images or []:
        url = str(url).strip()
        if url and all(v["url"] != url for v in variant["images"]):
            variant["images"].append(
                {"url": url, "alt": offer.name, "sort_order": len(variant["images"])}
            )


def _merge_variant(dst: dict, src: dict) -> None:
    """Sujungia src variantą į dst (union raktų kolizijos atveju)."""
    if not dst.get("name"):
        dst["name"] = src.get("name") or ""
    for warehouse, qty in src["stock"].items():
        dst["stock"][warehouse] = dst["stock"].get(warehouse, 0) + qty
    if not dst["ean"]:
        dst["ean"] = src["ean"]
    for img in src["images"]:
        if all(v["url"] != img["url"] for v in dst["images"]):
            img = dict(img, sort_order=len(dst["images"]))
            dst["images"].append(img)
    existing = {f["code"]: f for f in dst.get("features", [])}
    for f in src.get("features", []):
        existing.setdefault(f["code"], f)
    if existing:
        dst["features"] = list(existing.values())


def _generated_variant_name(
    offer, offers: list[Offer], variant_features: dict, profile
) -> str:
    """Fallback vardas, kai 'variantas' neteiktas: visų varijuojančių
    ašių label'iai (profilio tvarka, po to abėcėlės), pvz.
    'M · Black' / '0,20 mm · 4,5 kg · 150 m'."""
    if len(offers) <= 1:
        return ""
    metas = variant_features.get(offer.id) or {}
    if not metas:
        return ""
    order = [_code(ax) for ax in profile.axes]
    ordered = sorted(
        metas,
        key=lambda ax: (order.index(ax) if ax in order else len(order), ax),
    )
    return " · ".join(
        str(metas[ax].get("label") or "") for ax in ordered if metas[ax].get("label")
    )


def _build_variants(offers: list[Offer]) -> tuple[list[dict], dict[str, str], dict, dict]:
    """Variantai + demoted axes + option labels + option_value_meta."""
    profile = profile_for(_offer_context(offers[0])) if offers else DEFAULT_PROFILE
    options_by_offer, demoted, option_labels, variant_features, option_value_meta = (
        _plan_variants(offers, profile)
    )
    variants: list[dict] = []
    index: dict[str, dict] = {}
    for offer in offers:
        warehouse = _warehouse_code(offer.supplier)
        stock = max(offer.stock or 0, 0) if offer.is_available else 0
        keys = _variant_keys(offer)
        hits: list[dict] = []
        for key in keys:
            found = index.get(key)
            if found is not None and all(found is not h for h in hits):
                hits.append(found)
        if not hits:
            variant = _new_variant(
                offer,
                options_by_offer.get(offer.id, {}),
                variant_features.get(offer.id, {}),
                name=offer_variant_label(offer)
                or _generated_variant_name(offer, offers, variant_features, profile),
            )
            variants.append(variant)
        else:
            variant = hits.pop(0)
            for other in hits:
                if other in variants:
                    variants.remove(other)
                _merge_variant(variant, other)
                for idx_key, idx_val in index.items():
                    if idx_val is other:
                        index[idx_key] = variant
        if offer.ean and not variant["ean"]:
            variant["ean"] = offer.ean
        for key in keys:
            index[key] = variant
        variant["stock"][warehouse] = int(variant["stock"].get(warehouse, 0)) + int(stock)
        _add_offer_images(variant, offer)
    return variants, demoted, option_labels, option_value_meta


def _item_price_net(product: Product, offers: list[Offer]) -> str | None:
    """Item-level price_net – pirminio (default) varianto kaina."""
    primary = next(
        (o for o in offers if o.external_id == product.source_external_id),
        offers[0] if offers else None,
    )
    if primary is None:
        return None
    price = _net(
        primary.selling_price,
        primary.supplier.selling_price_includes_vat,
        primary.supplier.vat_rate,
    )
    return str(price) if price is not None else None


def _item_cost_net(product: Product, offers: list[Offer]) -> str | None:
    primary = next(
        (o for o in offers if o.external_id == product.source_external_id),
        offers[0] if offers else None,
    )
    cost = product.purchase_price
    if cost is None and primary is not None:
        cost = primary.purchase_price
    return str(cost.quantize(Decimal("0.01"))) if cost is not None else None


def build_catalog_payload(supplier_code: str | None = None) -> dict:
    base_lang = (Language.objects.filter(is_base=True).first())
    base_code = base_lang.code if base_lang else "en"
    tax_class = get_config("default_tax_class", DEFAULT_TAX_CLASS)

    products_qs = (
        Product.objects.filter(is_active=True)
        .select_related("brand", "category")
        .prefetch_related("texts__language", "offers__supplier")
        .filter(offers__is_published=True)
        .distinct()
    )
    if supplier_code:
        products_qs = products_qs.filter(offers__supplier__code=supplier_code)

    items: list[dict] = []
    _load_category_names()
    try:
        with collect():
            for product in products_qs.iterator(chunk_size=500):
                offers = [
                    offer
                    for offer in product.offers.all()
                    if offer.is_published and not offer.is_discontinued
                ]
                if supplier_code:
                    offers = [o for o in offers if o.supplier.code == supplier_code]
                if not offers:
                    continue

                variants, demoted_axes, option_labels, option_value_meta = (
                    _build_variants(offers)
                )
                supplier = offers[0].supplier.code if offers[0].supplier_id else ""
                context = _product_context(product)
                weight_g = None
                if product.shipping_weight_kg is not None:
                    weight_g = int((product.shipping_weight_kg * 1000).to_integral_value())

                items.append(
                    {
                        "code": product.sku,
                        "product_group_code": product.product_group or "",
                        "ean": product.ean or "",
                        "brand_code": product.brand.slug if product.brand_id else "",
                        "category": product.category.slug if product.category_id else "",
                        "tax_class": tax_class,
                        "is_active": product.is_active,
                        "price_net": _item_price_net(product, offers),
                        "cost_net": _item_cost_net(product, offers),
                        "weight_g": weight_g or 0,
                        "height_cm": _to_int_cm(product.shipping_height_cm),
                        "width_cm": _to_int_cm(product.shipping_width_cm),
                        "length_cm": _to_int_cm(product.shipping_depth_cm),
                        "translations": _translation_block(product, base_code),
                        "features": _features(
                            product, demoted_axes, context=context, supplier=supplier
                        ),
                        "additional_info": _additional_info(product),
                        "option_labels": option_labels,
                        "option_value_meta": option_value_meta,
                        "images": _images(product),
                        "videos": _videos(product),
                        "variants": variants,
                    }
                )

    finally:
        _CATEGORY_NAMES.clear()
    return {
        "format_version": FORMAT_VERSION,
        "source": "normalyzer",
        "mode": "upsert",
        "generated_at": timezone.now().isoformat(),
        "items": items,
    }


def build_stock_payload(supplier_code: str) -> dict:
    supplier = Supplier.objects.get(code=supplier_code)
    warehouse = _warehouse_code(supplier)
    offers = Offer.objects.filter(
        supplier=supplier,
        is_published=True,
        is_discontinued=False,
    )
    items = [
        {
            "code": _offer_variant_code(offer),
            "qty": max(offer.stock or 0, 0) if offer.is_available else 0,
        }
        for offer in offers.iterator()
    ]
    return {
        "format_version": FORMAT_VERSION,
        "warehouse_code": warehouse,
        "items": items,
    }


def payload_sha256(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


class ShopClient:
    """HTTP klientas shop ops API.

    Shop'as naudoja JWT per HttpOnly cookie (accounts.JWTAuth): login
    `POST /api/v1/auth/login` {email, password} -> `access_token` cookie
    (~15 min), refresh per `POST /api/v1/auth/refresh`.
    Reikia staff vartotojo (_require_ops_user: is_staff/is_superuser).
    """

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.SHOP_API_BASE_URL or "").rstrip("/")
        if not self.base_url:
            raise RuntimeError("SHOP_API_BASE_URL nenustatytas (.env).")
        self.session = requests.Session()
        self._logged_in = False

    def _login(self) -> None:
        email = getattr(settings, "SHOP_USER_EMAIL", "") or ""
        password = getattr(settings, "SHOP_USER_PASSWORD", "") or ""
        if not email or not password:
            raise RuntimeError("SHOP_USER_EMAIL / SHOP_USER_PASSWORD nenustatyti (.env).")
        response = self.session.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"email": email, "password": password},
            timeout=30,
        )
        response.raise_for_status()
        self._logged_in = True

    def post(self, path: str, payload: dict, timeout: int = 120) -> dict:
        if not self._logged_in:
            self._login()
        response = self.session.post(
            f"{self.base_url}{path}", json=payload, timeout=timeout
        )
        if response.status_code == 401:
            # access token galėjo baigtis – relogin ir kartojam
            self._login()
            response = self.session.post(
                f"{self.base_url}{path}", json=payload, timeout=timeout
            )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {"status": response.status_code, "body": response.text[:500]}

    def get(self, path: str, timeout: int = 60):
        if not self._logged_in:
            self._login()
        response = self.session.get(f"{self.base_url}{path}", timeout=timeout)
        if response.status_code == 401:
            self._login()
            response = self.session.get(f"{self.base_url}{path}", timeout=timeout)
        response.raise_for_status()
        return response.json()

    TERMINAL_STATUSES = {"done", "done_with_errors", "failed"}

    def wait_for_run(
        self,
        run_id: int,
        *,
        poll_interval: int | None = None,
        timeout: int | None = None,
    ) -> dict:
        """Poll'ina import run'ą iki terminalaus statuso."""
        interval = int(
            poll_interval
            if poll_interval is not None
            else getattr(settings, "SHOP_IMPORT_POLL_INTERVAL", 10)
        )
        wait_timeout = int(
            timeout
            if timeout is not None
            else getattr(settings, "SHOP_IMPORT_POLL_TIMEOUT", 3600)
        )
        deadline = time.monotonic() + wait_timeout
        last_status = "unknown"
        while True:
            data = self.get(f"/api/v1/ops/imports/runs/{run_id}")
            last_status = str(data.get("status") or "unknown")
            if last_status in self.TERMINAL_STATUSES:
                return data
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"shop import run #{run_id} still '{last_status}' "
                    f"after {wait_timeout}s"
                )
            time.sleep(interval)


def push_to_shop(path: str, payload: dict, timeout: int = 120) -> dict:
    """Push payload'ą su apply_async=true – atsako run'as, apply fone."""
    return ShopClient().post(f"{path}?apply_async=true", payload, timeout=timeout)


def run_export(
    kind: str,
    payload: dict,
    *,
    push: bool = False,
    file_name: str = "",
    triggered_by: str = "",
    options: dict | None = None,
) -> ExportRun:
    run = ExportRun.objects.create(
        kind=kind,
        file_name=file_name,
        file_sha256=payload_sha256(payload),
        item_count=len(payload.get("items", [])),
        options=options or {},
        triggered_by=triggered_by,
    )
    if push:
        path = CATALOG_PATH if kind == ExportRun.Kind.CATALOG else STOCK_PATH
        try:
            response = push_to_shop(path, payload)
            # Sinchroninis apply (maži feed'ai / senas shop) gali grąžinti
            # terminalų statusą iš karto – tada pollinti nereikia.
            if (
                response.get("id")
                and response.get("status") not in ShopClient.TERMINAL_STATUSES
            ):
                client = ShopClient()
                response = client.wait_for_run(response["id"])
            run.shop_response = response
            _apply_shop_result(run, response)
        except Exception as exc:
            logger.exception("Eksporto push nepavyko (run #%s)", run.pk)
            run.status = ExportRun.Status.FAILED
            run.error_message = str(exc)
    run.save()
    return run


def _apply_shop_result(run: ExportRun, response: dict) -> None:
    """Map'ina shop ImportRun statusą į ExportRun."""
    status = str(response.get("status") or "")
    if status == "failed":
        run.status = ExportRun.Status.FAILED
        run.error_message = str(
            response.get("error_message") or "shop import failed"
        )
        return
    run.status = ExportRun.Status.PUSHED
    if status == "done_with_errors":
        run.error_message = (
            f"shop import #{response.get('id')}: "
            f"{response.get('failed_items')} of "
            f"{response.get('total_items')} items failed"
        )
