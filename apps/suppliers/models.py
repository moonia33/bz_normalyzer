from django.db import models

from apps.core import TimeStampedModel


class SourceKind(models.TextChoices):
    XML_URL = "xml_url", "XML iš URL"
    JSON_URL = "json_url", "JSON iš URL"
    CSV_URL = "csv_url", "CSV iš URL"
    EXCEL_UPLOAD = "excel_upload", "Excel įkėlimas"
    CSV_UPLOAD = "csv_upload", "CSV įkėlimas"
    SCRAPER = "scraper", "Scrapinimas"
    API = "api", "Tiekėjo API"
    CUSTOM = "custom", "Individualus adapteris"


class Supplier(TimeStampedModel):
    code = models.SlugField(
        max_length=64,
        unique=True,
        help_text="Stabilus kodas. Individualiam adapteriui turi sutapti su adapterio code.",
    )
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

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
    is_active = models.BooleanField(default=True)
    last_success_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["supplier__name", "name"]
        verbose_name = "tiekėjo šaltinis"
        verbose_name_plural = "tiekėjų šaltiniai"

    def __str__(self) -> str:
        return f"{self.supplier.code}:{self.name}"
