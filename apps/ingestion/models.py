from django.db import models

from apps.catalog.models import Brand, Category, Product
from apps.core import TimeStampedModel
from apps.suppliers.models import Supplier, SupplierFeed


class ImportJob(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Laukia"
        RUNNING = "running", "Vykdoma"
        SUCCESS = "success", "Sėkmingai"
        FAILED = "failed", "Klaida"

    supplier = models.ForeignKey(Supplier, related_name="jobs", on_delete=models.CASCADE)
    feed = models.ForeignKey(
        SupplierFeed,
        null=True,
        blank=True,
        related_name="jobs",
        on_delete=models.SET_NULL,
    )
    uploaded_file = models.FileField(upload_to="imports/%Y/%m/", blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    stats = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "importo užduotis"
        verbose_name_plural = "importo užduotys"

    def __str__(self) -> str:
        return f"Job #{self.pk} {self.supplier.code} {self.status}"


class RawItem(models.Model):
    """Neliesta tiekėjo eilutė — diagnostikai ir per-apdorojimui."""

    job = models.ForeignKey(ImportJob, related_name="raw_items", on_delete=models.CASCADE)
    supplier = models.ForeignKey(Supplier, related_name="raw_items", on_delete=models.CASCADE)
    external_id = models.CharField(max_length=128, db_index=True)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["supplier", "external_id"]),
        ]
        verbose_name = "žalia eilutė"
        verbose_name_plural = "žalios eilutės"


class Offer(TimeStampedModel):
    """Normalizuotas tiekėjo pasiūlymas — vieningo sąrašo šaltinis API."""

    supplier = models.ForeignKey(Supplier, related_name="offers", on_delete=models.CASCADE)
    external_id = models.CharField(max_length=128)
    product = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        related_name="offers",
        on_delete=models.SET_NULL,
    )
    ean = models.CharField(max_length=32, blank=True, db_index=True)
    sku = models.CharField(max_length=128, blank=True)
    product_group = models.CharField(max_length=128, blank=True, db_index=True)
    name = models.CharField(max_length=500)
    base_name = models.CharField(max_length=500, blank=True)
    brand_name_raw = models.CharField(max_length=200, blank=True)
    brand = models.ForeignKey(Brand, null=True, blank=True, on_delete=models.SET_NULL)
    category_raw = models.CharField(max_length=500, blank=True, verbose_name="Suplier category")
    size_raw = models.CharField(max_length=200, blank=True, verbose_name="Supplier size")
    package_raw = models.CharField(max_length=120, blank=True, verbose_name="Supplier package")
    unit_weight_raw = models.CharField(max_length=120, blank=True, verbose_name="Supplier item weight")
    sub_category_raw = models.CharField(max_length=200, blank=True, verbose_name="Suplier sub category")
    family_raw = models.CharField(max_length=200, blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    main_image_url = models.URLField(blank=True, max_length=1000)
    description = models.TextField(blank=True)
    technical_description = models.TextField(blank=True)
    combined_full_text = models.TextField(blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    images = models.JSONField(default=list, blank=True)
    videos = models.JSONField(
        default=list,
        blank=True,
        help_text='[{"url": ..., "type": "product|instructional|tutorial", "language": "en"}]',
    )
    stock = models.IntegerField(default=0)
    availability_date = models.DateField(null=True, blank=True)
    purchase_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    rrp_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    shipping_height_cm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    shipping_width_cm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    shipping_depth_cm = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    shipping_weight_kg = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    currency = models.CharField(max_length=8, default="EUR")
    is_available = models.BooleanField(default=True)
    is_discontinued = models.BooleanField(
        default=False,
        help_text="Pažymi, kad tiekėjas prekę nutraukė (discontinued).",
    )
    is_published = models.BooleanField(
        default=False,
        help_text="Tik publikuoti pasiūlymai atiduodami per viešą API.",
    )
    content_hash = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text="Turinio hash – change detection (be stock/availability laukų).",
    )
    last_imported_at = models.DateTimeField(null=True, blank=True)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["supplier", "external_id"],
                name="uniq_offer_supplier_external_id",
            )
        ]
        indexes = [
            models.Index(fields=["is_published", "is_available"]),
            models.Index(fields=["ean"]),
        ]
        verbose_name = "pasiūlymas"
        verbose_name_plural = "pasiūlymai"

    def __str__(self) -> str:
        return f"{self.supplier.code}:{self.external_id} {self.name}"


class OfferText(models.Model):
    """Offer tekstai konkrečia kalba (kalbiniai feed'ai).

    Bazinės kalbos turinys – `Offer` laukuose. Kalbinis feed'as
    (feed.language != bazinė) rašo tik čia, neperrašydamas pagrindinių laukų.
    """

    offer = models.ForeignKey(Offer, related_name="texts", on_delete=models.CASCADE)
    language = models.ForeignKey("corecfg.Language", on_delete=models.CASCADE)
    name = models.CharField(max_length=500, blank=True)
    base_name = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    technical_description = models.TextField(blank=True)
    combined_full_text = models.TextField(blank=True)
    seo_title = models.CharField(max_length=255, blank=True)
    seo_description = models.CharField(max_length=320, blank=True)
    seo_keywords = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["offer", "language"],
                name="uniq_offer_text_language",
            )
        ]
        verbose_name = "pasiūlymo vertimas"
        verbose_name_plural = "pasiūlymų vertimai"

    def __str__(self) -> str:
        return f"{self.offer.external_id} [{self.language.code}]"
