from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import Product
from apps.catalog.profiles import profile_for
from apps.catalog_export import services as export_svc
from apps.catalog_export.services import strip_name_spec
from apps.ingestion.models import Offer
from apps.mapping.models import ProductLink


class Command(BaseCommand):
    help = (
        "Sujungia Product įrašus su tuo pačiu normalizuotu baziniu vardu\n"
        "(strip_name_spec nukerpa '(0.08mm)'/'150m' spec-uodegas) – tiekėjo\n"
        "feed'as kūrė atskirą produktą kiekvienam variantui (Guru Pulse 9\n"
        "Braid 0.08/0.10/0.12mm -> GP901/GP902/GP903).\n"
        "Grupė = (supplier, normalized_base_name, category); offer'iai ir\n"
        "link'ai -> kanoninis (žemiausias id), tekstai perkelti jei kalbos\n"
        "dar nėra, tuščios eilutės trinamos. Scope: 'lines' profilis.\n"
        "Dry-run pagal nutylėjimą; --apply pritaiko.\n"
        "PASTABA: shop'e senieji atskiri produktai lieka – upsert jų\n"
        "neištrina (nėra full_sync); pašalinti rankiniu prune."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true", help="Pritaikyti sujungimus"
        )
        parser.add_argument(
            "--supplier", help="Tik šio tiekėjo produktų grupės"
        )

    @transaction.atomic
    def handle(self, *args, **options):
        apply_changes = options["apply"]
        supplier_filter = (options.get("supplier") or "").strip()

        groups: dict[tuple, list] = defaultdict(list)
        export_svc._load_category_names()
        try:
            products = list(
                Product.objects.filter(is_active=True).prefetch_related(
                    "offers__supplier", "texts__language"
                )
            )
            for product in products:
                offers = list(product.offers.all())
                suppliers = sorted(
                    {o.supplier.code for o in offers if o.supplier_id}
                )
                if supplier_filter and supplier_filter not in suppliers:
                    continue
                context = export_svc._product_context(product, offers)
                if profile_for(context).code != "lines":
                    continue
                base = strip_name_spec(product.name).lower()
                if not base:
                    continue
                for supplier in suppliers or [""]:
                    groups[(supplier, base, product.category_id)].append(
                        product
                    )
        finally:
            export_svc._CATEGORY_NAMES.clear()

        candidates = []
        seen_groups: set[tuple] = set()
        for key, plist in groups.items():
            unique = {p.id: p for p in plist}
            if len(unique) < 2:
                continue
            members = tuple(sorted(unique))
            if members in seen_groups:
                continue
            seen_groups.add(members)
            candidates.append(sorted(unique.values(), key=lambda p: p.id))

        self.stdout.write(f"Merge kandidatų grupių: {len(candidates)}")
        merged_offers = 0
        for plist in candidates:
            canonical, rest = plist[0], plist[1:]
            rest_ids = [p.id for p in rest]
            offer_count = sum(p.offers.count() for p in rest)
            self.stdout.write(
                f"  {canonical.sku} ({canonical.name[:60]}) <- "
                + ", ".join(f"{p.sku}" for p in rest)
                + f"  [{offer_count} offer'iai]"
            )
            if not apply_changes:
                continue
            Offer.objects.filter(product_id__in=rest_ids).update(
                product=canonical
            )
            ProductLink.objects.filter(product_id__in=rest_ids).update(
                product=canonical
            )
            existing_langs = set(
                canonical.texts.values_list("language_id", flat=True)
            )
            for product in rest:
                for text in product.texts.all():
                    if text.language_id in existing_langs:
                        text.delete()
                    else:
                        text.product = canonical
                        text.save(update_fields=["product"])
                        existing_langs.add(text.language_id)
                canonical.categories.add(*product.categories.all())
            Product.objects.filter(id__in=rest_ids).delete()
            merged_offers += offer_count

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: {len(candidates)} grupių, "
                f"perkelta offer'ių: {merged_offers}"
            )
        )
