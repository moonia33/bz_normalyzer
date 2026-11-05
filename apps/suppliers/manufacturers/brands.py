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
            base = shopify.SITES[self.brand]
            yield f"{base}/products/", shopify.crawl(base, self.delay, self.limit)
        if (
            self.brand in sportfishtackle.SLUG_PREFIXES
            and sportfishtackle.LANGUAGE in languages
        ):
            yield (
                sportfishtackle.BASE,
                sportfishtackle.crawl(self.brand, self.delay, self.limit),
            )

    def crawl(
        self, languages=("de", "en"), delay: float = http.MIN_DELAY, limit: int = 0
    ):
        """`languages` riboja šaltinius (Shopify = en, sportfishtackle = de); `limit` – bendras.

        Po crawl `complete_prefixes` – pilnai nuskaitytų šaltinių URL prefiksai."""
        self.delay, self.limit = delay, limit
        self.complete_prefixes = []
        count = 0
        for prefix, source in self._sources(languages):
            while True:
                try:
                    item = next(source)
                except StopIteration as done:
                    if done.value:
                        self.complete_prefixes.append(prefix)
                    break
                yield item
                count += 1
                if limit and count >= limit:
                    return


BRANDS = {
    brand: BrandSource(brand)
    for brand in sorted(set(shopify.SITES) | set(sportfishtackle.SLUG_PREFIXES))
}
