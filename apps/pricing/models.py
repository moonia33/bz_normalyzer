from django.db import models

from apps.catalog.models import Brand, Category
from apps.core import TimeStampedModel
from apps.suppliers.models import Supplier


class PriceAction(models.TextChoices):
    MARKUP_PERCENT = "markup_percent", "Antkainis %"
    MARKUP_FIXED = "markup_fixed", "Antkainis suma"
    SET_PRICE = "set_price", "Fiksuota kaina"
    ROUND = "round", "Apvalinimas"


class RoundingMode(models.TextChoices):
    NONE = "none", "Be apvalinimo"
    CENT = "cent", "Iki cento"
    END_99 = "end_99", "Iki x.99"
    END_95 = "end_95", "Iki x.95"


class PriceRule(TimeStampedModel):
    """Taisyklės taikomos eilės tvarka pagal priority (mažesnis = anksčiau)."""

    name = models.CharField(max_length=200)
    priority = models.IntegerField(default=100)
    is_active = models.BooleanField(default=True)
    supplier = models.ForeignKey(
        Supplier,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text="Tuščia = visiems tiekėjams.",
    )
    category = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        help_text="Tuščia = visoms kategorijoms. Vaikinės kategorijos neįtraukiamos automatiškai.",
    )
    brand = models.ForeignKey(Brand, null=True, blank=True, on_delete=models.CASCADE)
    min_purchase_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    max_purchase_price = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    action = models.CharField(max_length=32, choices=PriceAction.choices)
    value = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        default=0,
        help_text="Procentas, suma arba fiksuota kaina, priklausomai nuo veiksmo.",
    )
    rounding = models.CharField(
        max_length=16,
        choices=RoundingMode.choices,
        default=RoundingMode.NONE,
    )

    class Meta:
        ordering = ["priority", "id"]
        verbose_name = "kainos taisyklė"
        verbose_name_plural = "kainos taisyklės"

    def __str__(self) -> str:
        return f"{self.priority}: {self.name}"
