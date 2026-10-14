from django.db import models

from apps.core import TimeStampedModel


class ExportRun(TimeStampedModel):
    """Eksporto į shop paleidimo auditas."""

    class Kind(models.TextChoices):
        CATALOG = "catalog", "Katalogas"
        STOCK = "stock", "Likučiai"

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
