from django.db import models
from django.utils.text import slugify

from apps.core import TimeStampedModel


class Brand(TimeStampedModel):
    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=220, unique=True, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "prekių ženklas"
        verbose_name_plural = "prekių ženklai"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Category(TimeStampedModel):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, blank=True)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="children",
        on_delete=models.CASCADE,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("parent", "slug")]
        verbose_name = "kategorija"
        verbose_name_plural = "kategorijos"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def path(self) -> str:
        names = [self.name]
        node = self.parent
        while node:
            names.append(node.name)
            node = node.parent
        return " > ".join(reversed(names))

    def __str__(self) -> str:
        return self.path()


class Product(TimeStampedModel):
    """Vieninga (kanoninė) prekė, nepriklausoma nuo tiekėjo."""

    sku = models.CharField(max_length=64, unique=True)
    source_external_id = models.CharField(max_length=128, blank=True, db_index=True)
    product_group = models.CharField(max_length=128, blank=True, db_index=True)
    name = models.CharField(max_length=500)
    base_name = models.CharField(max_length=500, blank=True)
    brand = models.ForeignKey(Brand, null=True, blank=True, on_delete=models.SET_NULL)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    category_raw = models.CharField(max_length=500, blank=True, verbose_name="Suplier category")
    size_raw = models.CharField(max_length=200, blank=True, verbose_name="Supplier size")
    package_raw = models.CharField(max_length=120, blank=True, verbose_name="Supplier package")
    unit_weight_raw = models.CharField(max_length=120, blank=True, verbose_name="Supplier item weight")
    categories = models.ManyToManyField(
        Category,
        related_name="products_assigned",
        blank=True,
    )
    sub_category_raw = models.CharField(max_length=200, blank=True, verbose_name="Suplier sub category")
    family_raw = models.CharField(max_length=200, blank=True)
    ean = models.CharField(max_length=32, blank=True, db_index=True)
    main_image_url = models.URLField(blank=True, max_length=1000)
    images = models.JSONField(default=list, blank=True)
    videos = models.JSONField(
        default=list,
        blank=True,
        help_text='[{"url": ..., "type": "product|instructional|tutorial", "language": "en"}]',
    )
    description = models.TextField(blank=True)
    technical_description = models.TextField(blank=True)
    combined_full_text = models.TextField(blank=True)
    seo_title = models.CharField(max_length=255, blank=True)
    seo_description = models.CharField(max_length=320, blank=True)
    seo_keywords = models.CharField(max_length=255, blank=True)
    purchase_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    rrp_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    shipping_height_cm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    shipping_width_cm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    shipping_depth_cm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    shipping_weight_kg = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "prekė"
        verbose_name_plural = "prekės"

    def __str__(self) -> str:
        return f"{self.sku} {self.name}"


class ProductText(TimeStampedModel):
    """Produkto tekstinė informacija konkrečia kalba (vertimas).

    Bazinė kalba (`Language.is_base`) saugoma `Product` laukuose;
    čia – papildomos kalbos iš kalbinių feed'ų arba AI vertimų.
    """

    product = models.ForeignKey(Product, related_name="texts", on_delete=models.CASCADE)
    language = models.ForeignKey("corecfg.Language", on_delete=models.CASCADE)
    name = models.CharField(max_length=500, blank=True)
    base_name = models.CharField(max_length=500, blank=True)
    slug = models.SlugField(max_length=220, blank=True)
    description = models.TextField(blank=True)
    technical_description = models.TextField(blank=True)
    combined_full_text = models.TextField(blank=True)
    seo_title = models.CharField(max_length=255, blank=True)
    seo_description = models.CharField(max_length=320, blank=True)
    seo_keywords = models.CharField(max_length=255, blank=True)
    source_offer = models.ForeignKey(
        "ingestion.Offer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="product_texts",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "language"],
                name="uniq_product_text_language",
            )
        ]
        verbose_name = "prekės vertimas"
        verbose_name_plural = "prekių vertimai"

    def __str__(self) -> str:
        return f"{self.product.sku} [{self.language.code}]"
