"""Prekių ženklo šaltiniai: gamintojo Shopify (EN) + sportfishtackle.de (DE).

ManufacturerContent.source = prekės ženklo slug (`slugify(brand_name_raw)`), nes
translate_lang ieško turinio pagal jį ir kodą (pirma EN, tada DE).
"""

from __future__ import annotations

from apps.suppliers.manufacturers import http, shopify, sportfishtackle


class BrandSource:
    def __init__(self, brand: str):
        self.brand = brand

    def _sources(self, languages):
        if self.brand in shopify.SITES and shopify.LANGUAGE in languages:
            yield shopify.crawl(shopify.SITES[self.brand], self.delay, self.limit)
        if (
            self.brand in sportfishtackle.SLUG_PREFIXES
            and sportfishtackle.LANGUAGE in languages
        ):
            yield sportfishtackle.crawl(self.brand, self.delay, self.limit)

    def crawl(
        self, languages=("de", "en"), delay: float = http.MIN_DELAY, limit: int = 0
    ):
        """`languages` riboja šaltinius (Shopify = en, sportfishtackle = de); `limit` – bendras."""
        self.delay, self.limit = delay, limit
        count = 0
        for source in self._sources(languages):
            for item in source:
                yield item
                count += 1
                if limit and count >= limit:
                    return


BRANDS = {
    brand: BrandSource(brand)
    for brand in sorted(set(shopify.SITES) | set(sportfishtackle.SLUG_PREFIXES))
}
