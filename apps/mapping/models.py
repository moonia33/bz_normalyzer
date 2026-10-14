from django.db import models

from apps.catalog.models import Brand, Category, Product
from apps.core import TimeStampedModel
from apps.suppliers.models import Supplier


class CategoryMapping(TimeStampedModel):
    supplier = models.ForeignKey(Supplier, related_name="category_mappings", on_delete=models.CASCADE)
    source_category_raw = models.CharField(max_length=200, verbose_name="Suplier category")
    source_sub_category_raw = models.CharField(max_length=200, blank=True, verbose_name="Suplier sub category")
    source_path = models.CharField(
        max_length=500,
        verbose_name="Suplier category > subcategory",
        help_text="Tiksli tiekėjo kategorijos eilutė, pvz. 'Meškerės > Spinningas'.",
    )
    category = models.ForeignKey(
        Category,
        related_name="source_mappings",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "source_category_raw", "source_sub_category_raw"],
                name="uniq_category_mapping_supplier_category_subcategory",
            ),
            models.UniqueConstraint(
                fields=["supplier", "source_path"],
                name="uniq_category_mapping_supplier_path",
            )
        ]
        verbose_name = "kategorijos mapinimas"
        verbose_name_plural = "kategorijų mapinimai"

    @staticmethod
    def build_source_path(source_category_raw: str, source_sub_category_raw: str) -> str:
        category_name = (source_category_raw or "").strip()
        sub_category_name = (source_sub_category_raw or "").strip()
        if not category_name:
            return ""
        if not sub_category_name:
            return category_name
        category_parts = [part.strip() for part in category_name.split(">") if part.strip()]
        if ">" in category_name and category_parts and category_parts[-1].lower() == sub_category_name.lower():
            return " > ".join(category_parts)
        return f"{category_name} > {sub_category_name}"

    def save(self, *args, **kwargs):
        source_path = (self.source_path or "").strip()
        if source_path:
            parts = [part.strip() for part in source_path.split(">") if part.strip()]
            if parts:
                self.source_category_raw = parts[0]
                self.source_sub_category_raw = " > ".join(parts[1:])
        self.source_path = self.build_source_path(
            self.source_category_raw,
            self.source_sub_category_raw,
        ) or source_path
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        mapped_path = self.category.path() if self.category_id else "-"
        return f"{self.supplier.code}: {self.source_path} → {mapped_path}"


class BrandMapping(TimeStampedModel):
    supplier = models.ForeignKey(Supplier, related_name="brand_mappings", on_delete=models.CASCADE)
    source_name = models.CharField(max_length=200)
    brand = models.ForeignKey(Brand, related_name="source_mappings", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "source_name"],
                name="uniq_brand_mapping_supplier_name",
            )
        ]
        verbose_name = "prekės ženklo mapinimas"
        verbose_name_plural = "prekės ženklų mapinimai"

    def __str__(self) -> str:
        return f"{self.supplier.code}: {self.source_name} → {self.brand}"


class ProductLink(TimeStampedModel):
    """Sąmoningas tiekėjo SKU sujungimas su kanonine preke."""

    supplier = models.ForeignKey(Supplier, related_name="product_links", on_delete=models.CASCADE)
    external_id = models.CharField(max_length=128)
    product = models.ForeignKey(Product, related_name="supplier_links", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "external_id"],
                name="uniq_product_link_supplier_external_id",
            )
        ]
        verbose_name = "prekės susiejimas"
        verbose_name_plural = "prekių susiejimai"

    def __str__(self) -> str:
        return f"{self.supplier.code}:{self.external_id} → {self.product.sku}"
