from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from lxml import etree

from apps.ingestion.dto import NormalizedItem
from apps.suppliers.adapters.base import AdapterError, BaseAdapter
from apps.suppliers.adapters.registry import register_adapter

logger = logging.getLogger(__name__)

DEFAULT_FEED_URL = "https://www.mivardi.com/xml/mivardivip"
DEFAULT_IMAGE_BASE = "https://www.mivardi.com/"

# Savikaina = RRP su PVM / (1+PVM) / (1+antkainis). Antkainis 50 %.
DEFAULT_MARKUP = Decimal("0.5")

# Čekiški PARAM vardai -> esamos ašys BE. Nemapuoti -> attributes["spec"].
PARAM_TO_AXIS: dict[str, str] = {
    "Velikost": "size",
    "Rozměr": "size",
    "Barva": "color",
    "Příchuť": "flavor",
    "Délka": "length",
    "Transportní délka": "unit_length",
    "Transportní rozměr": "unit_length",
    "Hmotnost": "weight",
    "Počet kusů v balení": "pack",
    "Počet dílů": "sections",
    "Nosnost": "strength",
    "Pevnost": "strength",
    "Objem": "volume",
    "Vrhací zátěž": "casting_weight",
    "Testovací křivka": "strength_lb",
    "Návin": "line_length",
    "Převodový poměr": "gear_ratio",
    "Šířka": "unit_width",
    "Výška": "unit_height",
    "Protihrot": "model",
}

# Průměr: < 1 -> valo skersmuo (line_diameter), >= 1 -> prekės dydis (size).
_DIAMETER_NUM = re.compile(r"[\d.,]+")

# Velikost ašis pagal kategorijos kontekstą.
_CONTEXT_AXIS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"Naviják", re.I), "Velikost", "reel_size"),
    (re.compile(r"Háčk", re.I), "Velikost", "hook_size"),
]

# Skaitinės reikšmės (su galimu vnt./skaitmenų sufiksu) – pack/sections ašims.
_NUMERIC_VALUE = re.compile(r"^\d+(?:\+\d+)?\s*(?:pcs?\.?|pc|vnt\.?)?$", re.I)

# Pavadinimo uodegos matmenys – grupavimo bazei nupjauti.
_NAME_MEAS = re.compile(
    r"(?:\s+[-–x]?\s*\d+(?:[.,]\d+)?\s*(?:x\s*\d+(?:[.,]\d+)?\s*)*"
    r"(?:mm|cm|m|g|kg|lb|lbs|oz|ft|')\b\.?)+$",
    re.I,
)
_WORD_RE = re.compile(r"[^A-Za-z0-9]+")


def _slug(text: str) -> str:
    text = _WORD_RE.sub("-", text.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", text)


def _text(node) -> str:
    if node is None or node.text is None:
        return ""
    return " ".join(node.text.split())


@register_adapter
class MivardiAdapter(BaseAdapter):
    """Mivardi (mivardi.com) viešo XML feed'o adapteris.

    GET https://www.mivardi.com/xml/mivardivip – be autentikacijos.
    config: url, image_base_url, stock_cap (>50 -> cap), markup (0.5),
    vat_rate (0.21), brand, param_to_axis (pakeičia numatytą lentelę),
    login_url/username/password (nebūtina – viešas feed'as).
    """

    code = "mivardi"

    def fetch(self, uploaded_path: str | None = None) -> bytes:
        if uploaded_path or self.feed.file:
            return super().fetch(uploaded_path)
        import requests

        url = self.feed.url or str(self.config.get("url") or DEFAULT_FEED_URL)
        timeout = int(self.config.get("timeout", 120))
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (BaltijosZvynas importer)"})

        # Viešas feed'as; jei kažkada užsidarytų – fallback login'as
        username = str(self.config.get("username") or "")
        password = str(self.config.get("password") or "")
        if username and password:
            login_url = str(
                self.config.get("login_url")
                or "https://www.mivardi.com/eshop/business/sign/in"
            )
            try:
                page = session.get(login_url, timeout=timeout)
                token = re.search(r'name="_token_" value="([^"]+)"', page.text)
                session.post(
                    login_url,
                    data={
                        "email": username,
                        "password": password,
                        "_token_": token.group(1) if token else "",
                        "_do": "businessSignInForm-form-submit",
                    },
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                logger.warning("Mivardi login nepavyko (tęsiama viešai): %s", exc)

        response = session.get(url, timeout=timeout)
        response.raise_for_status()
        return response.content

    def parse(self, payload: bytes) -> Iterator[dict[str, Any]]:
        root = etree.fromstring(payload)
        param_map = self._param_map()
        rows: list[dict[str, Any]] = []
        for item in root.findall("SHOPITEM"):
            row = {
                "code": _text(item.find("PRODUCTNO")),
                "name": _text(item.find("PRODUCT")),
                "price_vat": _text(item.find("PRICE_VAT")),
                "vat": _text(item.find("VAT")),
                "original_price": _text(item.find("ORIGINAL_PRICE")),
                "stock_flag": _text(item.find("STOCK")),
                "stock_quantity": _text(item.find("STOCK_QUANTITY")),
                "description": _text(item.find("DESCRIPTION")),
                "html_content": (item.find("HTML_CONTENT").text or "").strip()
                if item.find("HTML_CONTENT") is not None
                else "",
                "mcarp": _text(item.find("MCARP")),
                "ean": _text(item.find("EAN")),
                "category_text": _text(item.find("CATEGORYTEXT")),
                "video_url": _text(item.find("VIDEO_URL")),
                "images": [
                    _text(img) for img in item.findall("IMAGES/IMGURL") if _text(img)
                ],
                "axes": {},
                "spec": {},
            }
            params = item.find("PARAMETERS")
            if params is not None:
                for param in params.findall("PARAM"):
                    name = (param.get("name") or "").strip()
                    value = _text(param)
                    if not name or not value:
                        continue
                    axis = self._param_axis(name, value, row["category_text"], param_map)
                    if axis and not self._axis_value_ok(axis, value):
                        axis = ""
                    if axis:
                        row["axes"][axis] = value
                    else:
                        row["spec"][name] = value
            rows.append(row)

        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            base = _NAME_MEAS.sub("", row["name"]).strip(" -–—,")
            row["base"] = base or row["name"]
            leaf = row["category_text"].split("|")[-1].strip().lower()
            groups[(leaf, row["base"].lower())].append(row)

        for (_leaf, base_key), members in groups.items():
            group_code = _slug(members[0]["base"])[:64] if len(members) > 1 else ""
            for member in members:
                member["product_group"] = group_code
                yield member

    def _param_map(self) -> dict[str, str]:
        custom = self.config.get("param_to_axis")
        if custom:
            merged = dict(PARAM_TO_AXIS)
            merged.update(custom)
            return merged
        return PARAM_TO_AXIS

    def _param_axis(
        self, name: str, value: str, category_text: str, param_map: dict[str, str]
    ) -> str:
        for pattern, param_name, axis in _CONTEXT_AXIS:
            if name == param_name and pattern.search(category_text or ""):
                return axis
        if name == "Průměr":
            match = _DIAMETER_NUM.search(value)
            num = self._to_decimal(match.group(0)) if match else None
            if num is not None and num < Decimal("1"):
                return "line_diameter"
            return "size"
        return param_map.get(name, "")

    @staticmethod
    def _axis_value_ok(axis: str, value: str) -> bool:
        # pack/sections = tik skaitinės reikšmės; turinio aprašymai -> spec.
        if axis in {"pack", "sections"}:
            return bool(_NUMERIC_VALUE.match(value.strip()))
        return True

    def normalize(self, row: dict[str, Any]) -> NormalizedItem:
        code = (row.get("code") or "").strip()
        if not code:
            raise AdapterError(f"Mivardi: trūksta PRODUCTNO: {repr(row)[:200]}")
        name = (row.get("name") or "").strip()
        if not name:
            raise AdapterError(f"Mivardi: tuščias PRODUCT ({code})")

        brand = str(self.config.get("brand") or "Mivardi").strip()
        base = (row.get("base") or name).strip()
        base_name = base
        if brand and brand.lower() not in base.lower().split():
            base_name = f"{brand} {base}".strip()

        rrp_vat = self._to_decimal(row.get("price_vat"))
        rrp = self._to_decimal(row.get("original_price")) or rrp_vat
        purchase = self._purchase_price(rrp_vat, row.get("vat"))

        qty_raw = (row.get("stock_quantity") or "").strip()
        stock_flag = (row.get("stock_flag") or "").strip() == "1"
        # STOCK=0 -> out of stock nepriklausomai nuo qty (45 prekės feed'e
        # turi liekamą qty, bet jau neparduodamos). ">50" -> stock_cap (51).
        stock = self._stock_qty(qty_raw) if stock_flag else 0
        is_available = stock_flag and stock > 0

        images = [self._image_url(u) for u in row.get("images") or []]
        axes = row.get("axes") or {}
        attributes: dict[str, Any] = {}
        if axes:
            attributes["axes"] = axes
        if row.get("spec"):
            attributes["spec"] = row["spec"]

        description = (row.get("html_content") or "").strip() or (
            row.get("description") or ""
        )
        category_path = " > ".join(
            part.strip()
            for part in (row.get("category_text") or "").split("|")
            if part.strip()
        )

        extra = {
            "identifiers": {
                "art_no": code,
                "product_group": row.get("product_group") or code,
            },
            "base_name": base_name,
            "product_group": row.get("product_group") or "",
            "sub_category_raw": (row.get("category_text") or "")
            .split("|")[-1]
            .strip(),
            "rrp_price": str(rrp) if rrp is not None else None,
            "main_image_url": images[0] if images else "",
            "mcarp": row.get("mcarp") == "1",
            "stock_text": qty_raw,
            "size_raw": axes.get("size"),
            "unit_weight_raw": axes.get("weight"),
            "package_raw": axes.get("pack"),
        }
        videos = []
        if row.get("video_url"):
            videos.append(
                {"url": row["video_url"], "type": "tutorial", "language": "en"}
            )
        return NormalizedItem(
            external_id=code,
            sku=code,
            name=name,
            ean=row.get("ean") or "",
            brand_name=brand,
            category_path=category_path,
            description=description,
            attributes=attributes,
            images=images,
            videos=videos,
            stock=stock,
            purchase_price=purchase,
            currency="EUR",
            is_available=is_available,
            extra={k: v for k, v in extra.items() if v not in (None, "", {})},
            raw=self._clean_dict(row),
        )

    def _purchase_price(self, rrp_vat: Decimal | None, vat_raw: Any) -> Decimal | None:
        """Savikaina = RRP_su_PVM / (1+vat) / (1+markup)."""
        if rrp_vat is None:
            return None
        vat = self._to_decimal(vat_raw)
        vat_rate = (vat / Decimal("100")) if vat is not None else Decimal(
            str(self.config.get("vat_rate", "0.21"))
        )
        markup = Decimal(str(self.config.get("markup", str(DEFAULT_MARKUP))))
        net = rrp_vat / (Decimal("1") + vat_rate)
        return (net / (Decimal("1") + markup)).quantize(Decimal("0.0001"))

    def _stock_qty(self, raw: str) -> int:
        if not raw:
            return 0
        if raw.startswith(">"):
            return int(self.config.get("stock_cap", 51))
        return self._to_int(raw)

    def _image_url(self, path: str) -> str:
        base = str(self.config.get("image_base_url") or DEFAULT_IMAGE_BASE)
        return urljoin(base, path)

    def _clean_dict(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): self._clean_dict(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._clean_dict(v) for v in value]
        if isinstance(value, Decimal):
            return float(value)
        return value
