"""Prekių ženklo šaltiniai: gamintojo Shopify (EN) + sportfishtackle.de (DE).

ManufacturerContent.source = prekės ženklo slug (`slugify(brand_name_raw)`), nes
translate_lang ieško turinio pagal jį ir kodą (pirma EN, tada DE).
"""

from __future__ import annotations

from apps.suppliers.manufacturers import http, shopify, sportfishtackle


class BrandSource:
    def __init__(self, brand: str):
        self.brand = brand

    def crawl(self, languages=(), delay: float = http.MIN_DELAY, limit: int = 0):
        if self.brand in shopify.SITES:
            yield from shopify.crawl(shopify.SITES[self.brand], delay, limit)
        if self.brand in sportfishtackle.SLUG_PREFIXES:
            yield from sportfishtackle.crawl(self.brand, delay, limit)


BRANDS = {
    brand: BrandSource(brand)
    for brand in sorted(set(shopify.SITES) | set(sportfishtackle.SLUG_PREFIXES))
}
