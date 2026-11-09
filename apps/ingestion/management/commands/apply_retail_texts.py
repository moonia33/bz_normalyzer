"""Tiekėjo parduotuvės (nemus.lt) LT aprašymas -> OfferText / ProductText be vertimo.

Vertimas (translate_lang --apply) tai daro pats; ši komanda – jau išverstoms prekėms."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from apps.catalog.models import ProductText
from apps.corecfg.models import Language
from apps.ingestion.management.commands.translate_lang import (
    Command as TranslateCommand,
)
from apps.ingestion.management.commands.translate_lang import (
    product_retail_description,
    retail_description,
)
from apps.ingestion.models import Offer, OfferText


class Command(BaseCommand):
    help = "Įrašo tiekėjo parduotuvės aprašymą (pvz. nemus.lt) į tos kalbos tekstus"

    def add_arguments(self, parser):
        parser.add_argument("--supplier", required=True, help="Tiekėjo kodas")
        parser.add_argument("--language", default="lt", help="Kalbos kodas")
        parser.add_argument("--dry-run", action="store_true", help="Tik statistika, nieko nerašyti")

    def handle(self, *args, **options):
        lang = Language.objects.get(code=options["language"])
        offers = (
            Offer.objects.filter(supplier__code=options["supplier"])
            .select_related("supplier")
            .order_by("id")
        )
        stats = {"offers": 0, "offer_texts": 0, "product_texts": 0}
        products: set[int] = set()
        with transaction.atomic():
            for offer in offers.iterator():
                description = retail_description(offer, lang.code)
                if not description:
                    continue
                stats["offers"] += 1
                updated = OfferText.objects.filter(offer=offer, language=lang).exclude(
                    description=description
                ).update(description=description)
                stats["offer_texts"] += updated
                if offer.product_id:
                    products.add(offer.product_id)
            for product_id in products:
                if TranslateCommand._has_variants(product_id):
                    description = product_retail_description(product_id, lang.code)
                    texts = ProductText.objects.filter(product_id=product_id, language=lang, source_offer=None)
                else:
                    offer = Offer.objects.filter(product_id=product_id, is_published=True).first()
                    if offer is None:
                        continue
                    description = retail_description(offer, lang.code)
                    texts = ProductText.objects.filter(product_id=product_id, language=lang).filter(
                        Q(source_offer__isnull=True) | Q(source_offer=offer)
                    )
                if description:
                    stats["product_texts"] += texts.exclude(description=description).update(
                        description=description
                    )
            if options["dry_run"]:
                transaction.set_rollback(True)
        prefix = "DRY-RUN " if options["dry_run"] else ""
        self.stdout.write(self.style.SUCCESS(f"{prefix}{stats}"))
