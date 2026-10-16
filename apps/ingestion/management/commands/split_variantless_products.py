from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count
from django.utils.text import slugify

from apps.catalog.models import Product, ProductText
from apps.ingestion.models import Offer
from apps.mapping.models import ProductLink

PRODUCT_COPY_FIELDS = [
    "base_name",
    "category_raw",
    "sub_category_raw",
    "family_raw",
    "size_raw",
    "package_raw",
    "unit_weight_raw",
    "main_image_url",
    "description",
    "technical_description",
    "combined_full_text",
    "purchase_price",
    "rrp_price",
    "shipping_height_cm",
    "shipping_width_cm",
    "shipping_depth_cm",
    "shipping_weight_kg",
]

TEXT_COPY_FIELDS = [
    "name",
    "base_name",
    "description",
    "technical_description",
    "seo_title",
    "seo_description",
    "seo_keywords",
]


def _unique_sku(base: str, supplier_code: str, taken: set[str]) -> str:
    """sku = art-no; supplier prefiksas tik jei art-no jau užimtas."""
    sku = base[:64]
    if sku not in taken:
        taken.add(sku)
        return sku
    sku = f"{supplier_code}-{base}"[:64]
    taken.add(sku)
    return sku


class Command(BaseCommand):
    help = (
        "Išskaldo produktus, kurie klaidingai sugrupavo offer'ius be product_group\n"
        "(atsarginės dalys ir t.t.): kiekvienas offer'is -> atskiras Product.\n"
        "Papildomai pervadina 'supplier-' prefiksuotus SKU į art-no, kai laisva.\n"
        "Numatytai dry-run; --apply pritaiko."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Pritaikyti pakeitimus")

    @transaction.atomic
    def handle(self, *args, **options):
        apply_changes = options["apply"]
        taken_skus = set(Product.objects.values_list("sku", flat=True))

        bad_products = []
        for product in (
            Product.objects.annotate(n=Count("offers"))
            .filter(n__gte=2)
            .prefetch_related("offers__supplier", "offers__texts__language")
        ):
            offers = list(product.offers.all())
            if all(not offer.product_group for offer in offers):
                bad_products.append((product, offers))

        self.stdout.write(f"Produktų išskaidymui: {len(bad_products)}")
        renamed = 0
        split_offers = 0

        for product, offers in bad_products:
            self.stdout.write(
                f"  {product.sku} ({product.name[:50]}) -> {len(offers)} offer'iai"
            )
            if not apply_changes:
                continue
            for offer in offers:
                sku = _unique_sku(offer.external_id, offer.supplier.code, taken_skus)
                fields = {f: getattr(offer, f) for f in PRODUCT_COPY_FIELDS}
                new_product = Product.objects.create(
                    sku=sku,
                    source_external_id=offer.external_id,
                    name=offer.name,
                    ean=offer.ean or "",
                    brand=offer.brand,
                    category=offer.category,
                    attributes=offer.attributes or {},
                    images=list(offer.images) if isinstance(offer.images, list) else [],
                    videos=list(offer.videos) if isinstance(offer.videos, list) else [],
                    **fields,
                )
                if offer.category_id:
                    new_product.categories.add(offer.category)
                ProductLink.objects.filter(
                    supplier=offer.supplier, external_id=offer.external_id
                ).update(product=new_product)
                for text in offer.texts.all():
                    defaults = {f: getattr(text, f) for f in TEXT_COPY_FIELDS}
                    ProductText.objects.create(
                        product=new_product,
                        language=text.language,
                        source_offer=offer,
                        slug=slugify(text.name)[:220] if text.name else "",
                        **defaults,
                    )
                offer.product = new_product
                offer.save(update_fields=["product", "updated_at"])
                split_offers += 1
            product.delete()

        # 'supplier-' prefiksuoti SKU -> art-no, kai laisva
        supplier_codes = list(
            ProductLink.objects.values_list("supplier__code", flat=True).distinct()
        )
        for product in Product.objects.exclude(sku=""):
            for code in supplier_codes:
                prefix = f"{code}-"
                if product.sku.startswith(prefix):
                    bare = product.sku[len(prefix):]
                    if bare and bare not in taken_skus:
                        if apply_changes:
                            taken_skus.discard(product.sku)
                            product.sku = bare[:64]
                            product.save(update_fields=["sku", "updated_at"])
                            taken_skus.add(product.sku)
                        renamed += 1
                    break

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(self.style.SUCCESS(
            f"{mode}: išskaidyta {split_offers} offer'ių iš {len(bad_products)} produktų, "
            f"pervadinta SKU: {renamed}"
        ))
