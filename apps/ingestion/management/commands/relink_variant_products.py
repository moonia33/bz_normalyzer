from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalog.models import Product
from apps.ingestion.models import Offer
from apps.ingestion.pipeline import _find_matching_product
from apps.mapping.models import ProductLink
from apps.suppliers.models import Supplier


class Command(BaseCommand):
    help = (
        "Pervertina Offer->Product ryšius pagal dabartinius\n"
        "offer.base_name / product_group / sub_category laukus, ignoruodama\n"
        "senus ProductLink įrašus (jie 'užstringa' ir blokuoja\n"
        "pergrupavimą po adapterio parsing pataisymų – pvz. Korda Guru\n"
        "kabliukai, kur kiekvienas dydis gavo atskirą produktą).\n"
        "Offer'is ir jo ProductLink perkeliami į rastą produktą; likę\n"
        "produktai be offer'ių ir be link'ų – trinami. Kitų tiekėjų\n"
        "offer'iams produktas nekeičiamas.\n"
        "Dry-run pagal nutylėjimą; --apply pritaiko."
    )

    def add_arguments(self, parser):
        parser.add_argument("--supplier", required=True, help="Tiekėjo kodas")
        parser.add_argument(
            "--apply", action="store_true", help="Pritaikyti perkėlimus"
        )

    @transaction.atomic
    def handle(self, *args, **options):
        supplier = Supplier.objects.get(code=options["supplier"])
        apply_changes = options["apply"]

        offers = list(
            Offer.objects.filter(supplier=supplier)
            .exclude(product_group="")
            .exclude(base_name="")
            .select_related("product")
            .order_by("pk")
        )
        moves: list[tuple[Offer, Product, Product | None]] = []
        for offer in offers:
            target = _find_matching_product(
                supplier,
                offer.external_id,
                offer,
                use_links=False,
                use_identity=False,
            )
            if target and target.id != offer.product_id:
                moves.append((offer, target, offer.product))

        self.stdout.write(f"Offer'ių perkėlimui: {len(moves)}")
        for offer, target, source in moves:
            self.stdout.write(
                f"  {offer.external_id} ({offer.name[:45]}) "
                f"P{offer.product_id} -> P{target.id} ({target.name[:45]})"
            )

        if apply_changes:
            source_ids = {s.id for _, _, s in moves if s}
            for offer, target, source in moves:
                offer.product = target
                offer.save(update_fields=["product", "updated_at"])
                ProductLink.objects.filter(
                    supplier=supplier, external_id=offer.external_id
                ).update(product=target)
            # Orphan produktai: šaltinio produktai, iš kurių iškėlėme
            # paskutinį offer'į, + produktai be jokio offer'o, kuriuos
            # šis tiekėjas anksčiau paliko (link'ai jau perkelti).
            orphan_list = list(
                Product.objects.filter(
                    id__in=source_ids, offers__isnull=True
                )
            ) + list(
                Product.objects.filter(
                    supplier_links__supplier=supplier, offers__isnull=True
                ).exclude(id__in=source_ids).distinct()
            )
        else:
            # Dry-run – prognozuojami orphan'ai: produktai, kurių visi
            # offer'iai būtų perkelti (įskaitant kitų tiekėjų – jie čia
            # neperkelti, tad produktas su svetimais offer'iais lieka).
            moved_offer_ids = {offer.id for offer, _, _ in moves}
            source_ids = {s.id for _, _, s in moves if s}
            orphan_list = []
            for product in Product.objects.filter(
                id__in=source_ids
            ).prefetch_related("offers"):
                if all(o.id in moved_offer_ids for o in product.offers.all()):
                    orphan_list.append(product)
            orphan_list += list(
                Product.objects.filter(
                    supplier_links__supplier=supplier, offers__isnull=True
                ).exclude(id__in=source_ids).distinct()
            )

        for product in orphan_list:
            self.stdout.write(
                f"  orphan P{product.id} {product.sku} ({product.name[:45]})"
            )
            if apply_changes:
                ProductLink.objects.filter(product=product).delete()
                product.delete()

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: perkelta {len(moves)} offer'ių, "
                f"ištrinta orphan produktų: {len(orphan_list)}"
            )
        )
