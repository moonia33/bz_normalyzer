from django.db import models

from apps.core import TimeStampedModel


class ExportRun(TimeStampedModel):
    """Eksporto į shop paleidimo auditas."""

    class Kind(models.TextChoices):
        CATALOG = "catalog", "Katalogas"
        STOCK = "stock", "Likučiai"
        PRICE = "price", "Kainos"

    class Status(models.TextChoices):
        DONE = "done", "Atlikta"
        PUSHED = "pushed", "Nusiųsta į shop"
        FAILED = "failed", "Klaida"

    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.CATALOG)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DONE)
    file_name = models.CharField(max_length=300, blank=True)
    file_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    item_count = models.IntegerField(default=0)
    options = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    shop_response = models.JSONField(default=dict, blank=True)
    triggered_by = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "eksporto paleidimas"
        verbose_name_plural = "eksporto paleidimai"

    def __str__(self) -> str:
        return f"{self.kind} #{self.pk} {self.status} ({self.item_count})"


class PipelineRun(TimeStampedModel):
    """Vieno tiekėjo pipeline etapo (catalog/prices/stock) paleidimo žurnalas."""

    class Stage(models.TextChoices):
        CATALOG = "catalog", "Katalogas"
        PRICES = "prices", "Kainos"
        STOCK = "stock", "Likučiai"

    class Status(models.TextChoices):
        RUNNING = "running", "Vykdoma"
        WAITING = "waiting", "Laukia vertimo"
        DONE = "done", "Atlikta"
        FAILED = "failed", "Klaida"

    supplier = models.ForeignKey(
        "suppliers.Supplier", related_name="pipeline_runs", on_delete=models.CASCADE
    )
    stage = models.CharField(max_length=16, choices=Stage.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    dry_run = models.BooleanField(default=False)
    details = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "pipeline paleidimas"
        verbose_name_plural = "pipeline paleidimai"

    def __str__(self) -> str:
        return f"{self.supplier_id}:{self.stage} #{self.pk} {self.status}"

