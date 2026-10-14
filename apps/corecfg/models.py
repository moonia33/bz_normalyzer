from django.db import models

from apps.core import TimeStampedModel


class Language(TimeStampedModel):
    """Palaikomos katalogo kalbos.

    `is_base` žymi kalbą, kuria saugomas normalizuotas turinys
    `Offer`/`Product` pagrindiniuose laukuose (šiuo metu – tiekėjų feed'ų
    bazinė kalba, EN). Kitos kalbos laikomos `OfferText`/`ProductText`
    vertimų lentelėse.
    """

    code = models.SlugField(max_length=8, unique=True)
    name = models.CharField(max_length=64)
    is_base = models.BooleanField(
        default=False,
        help_text="Bazinė normalizavimo kalba – Offer/Product pagrindiniai laukai.",
    )
    is_active = models.BooleanField(default=True)
    position = models.IntegerField(default=100)

    class Meta:
        ordering = ["position", "code"]
        verbose_name = "kalba"
        verbose_name_plural = "kalbos"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_base:
            Language.objects.filter(is_base=True).exclude(pk=self.pk).update(is_base=False)

    def __str__(self) -> str:
        return f"{self.code} ({self.name})"


class ConfigEntry(TimeStampedModel):
    """Bendros normalyzerio/shopo konfigūracijos reikšmės (key → JSON).

    Naudojama suvienodinti su shop'u dalinamoms reikšmėms:
    tax_class kodai, warehouse kodai, eksporto nustatymai ir pan.
    """

    key = models.SlugField(max_length=100, unique=True)
    value = models.JSONField(default=dict, blank=True)
    description = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["key"]
        verbose_name = "konfigūracijos įrašas"
        verbose_name_plural = "konfigūracijos įrašai"

    def __str__(self) -> str:
        return self.key


def get_config(key: str, default=None):
    entry = ConfigEntry.objects.filter(key=key).first()
    if entry is None:
        return default
    return entry.value
