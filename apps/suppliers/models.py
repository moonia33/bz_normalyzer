from django.db import models
from django.utils import timezone

from apps.core import TimeStampedModel


class SourceKind(models.TextChoices):
    XML_URL = "xml_url", "XML iš URL"
    JSON_URL = "json_url", "JSON iš URL"
    CSV_URL = "csv_url", "CSV iš URL"
    EXCEL_URL = "excel_url", "Excel iš URL"
    EXCEL_UPLOAD = "excel_upload", "Excel įkėlimas"
    CSV_UPLOAD = "csv_upload", "CSV įkėlimas"
    SCRAPER = "scraper", "Scrapinimas"
    API = "api", "Tiekėjo API"
    CUSTOM = "custom", "Individualus adapteris"


class FeedRole(models.TextChoices):
    CATALOG = "catalog", "Katalogas (pilni duomenys)"
    STOCK = "stock", "Likučiai / prieinamumas"
    DISCONTINUED = "discontinued", "Nutrauktos prekės"


class Supplier(TimeStampedModel):
    code = models.SlugField(
        max_length=64,
        unique=True,
        help_text="Stabilus kodas. Individualiam adapteriui turi sutapti su adapterio code.",
    )
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    vat_rate = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default="0.21",
        help_text="PVM tarifas, naudojamas kainų konvertavimui į neto.",
    )
    selling_price_includes_vat = models.BooleanField(
        default=True,
        help_text="Jei taip – selling_price yra su PVM; eksportui skaičiuojamas price_net.",
    )
    purchase_price_includes_vat = models.BooleanField(
        default=False,
        help_text="Jei taip – feed'o purchase_price yra su PVM; importe konvertuojama į neto.",
    )
    export_warehouse_code = models.SlugField(
        max_length=64,
        blank=True,
        help_text="Shop'o warehouse_code, į kurį rašomi šio tiekėjo likučiai. Tuščia = supplier.code.",
    )
    sku_prefix = models.CharField(
        max_length=16,
        blank=True,
        help_text="Prefiksas eksportuojamiems variantų kodams (pvz. 'korda-'), "
                  "kad skirtingų tiekėjų sutampantys art-no nesikirstų shop'e.",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "tiekėjas"
        verbose_name_plural = "tiekėjai"

    def __str__(self) -> str:
        return self.name


class SupplierFeed(TimeStampedModel):
    """Vienas tiekėjas gali turėti kelis šaltinius (pvz. XML + rankinis Excel)."""

    supplier = models.ForeignKey(Supplier, related_name="feeds", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=32, choices=SourceKind.choices)
    role = models.CharField(
        max_length=32,
        choices=FeedRole.choices,
        default=FeedRole.CATALOG,
        help_text="catalog = pilni prekės duomenys; stock = tik likučiai; discontinued = nutrauktos.",
    )
    language = models.ForeignKey(
        "corecfg.Language",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="feeds",
        help_text="Feed'o tekstų kalba. Bazinės kalbos feed'as rašo Offer laukus; kitų kalbų feed'ai – OfferText vertimus.",
    )
    feed_group = models.SlugField(
        max_length=64,
        blank=True,
        help_text="Vienos grupės feed'ai (visos kalbos) importuojami kartu, bazinė kalba pirmiausia.",
    )
    url = models.URLField(blank=True)
    file = models.FileField(upload_to="feeds/%Y/%m/", blank=True)
    adapter_code = models.SlugField(
        max_length=64,
        blank=True,
        help_text="Palikus tuščią, ieškoma adapterio pagal tiekėjo kodą, tada generic pagal kind.",
    )
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Laukų mapinimas, XPath, lapo pavadinimas, HTTP antraštės ir pan.",
    )
    sync_interval_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Automatinio importo periodas minutėmis. Tuščia = tik rankinis importas.",
    )
    is_active = models.BooleanField(default=True)
    last_success_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["supplier__name", "name"]
        verbose_name = "tiekėjo šaltinis"
        verbose_name_plural = "tiekėjų šaltiniai"

    def is_due(self, now=None) -> bool:
        if not self.is_active or not self.sync_interval_minutes:
            return False
        if self.last_success_at is None:
            return True
        now = now or timezone.now()
        return (now - self.last_success_at).total_seconds() >= self.sync_interval_minutes * 60

    def __str__(self) -> str:
        return f"{self.supplier.code}:{self.name}"
