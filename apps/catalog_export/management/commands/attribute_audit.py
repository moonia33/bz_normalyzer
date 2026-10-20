from collections import Counter

from django.core.management.base import BaseCommand

from apps.catalog.models import UnmappedAttribute
from apps.catalog_export.services import build_catalog_payload


class Command(BaseCommand):
    help = (
        "Dry-run katalogo eksportas + UnmappedAttribute ataskaita: "
        "kiekvienam tiekėjui kiek atributų reikšmių išeksportuota, "
        "kiek numesta kaip unmapped, suspicious/unparsed suvestinė."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--supplier", help="Audituoti tik vieno tiekėjo prekes"
        )
        parser.add_argument(
            "--top", type=int, default=15, help="Top N unmapped kodų"
        )

    def handle(self, *args, **options):
        supplier_code = options.get("supplier")
        # Švarus snapshot'as: eksportas perrašo count/sample_values.
        UnmappedAttribute.objects.all().delete()
        payload = build_catalog_payload(supplier_code=supplier_code)

        exported = 0
        per_item_features = 0
        for item in payload["items"]:
            per_item_features += len(item.get("features") or [])
            for variant in item.get("variants") or []:
                exported += len(variant.get("options") or {})
                per_item_features += len(variant.get("features") or [])
            exported += len(item.get("features") or [])

        qs = UnmappedAttribute.objects.all()
        if supplier_code:
            qs = qs.filter(supplier__code=supplier_code)

        self.stdout.write(f"Items: {len(payload['items'])}")
        self.stdout.write(f"Exported attribute values (options+features): {exported}")

        # Suvestinė per tiekėją
        per_supplier = Counter()
        per_kind = Counter()
        for ua in qs.select_related("supplier"):
            per_supplier[ua.supplier.code if ua.supplier else "-"] += ua.count
            per_kind[ua.kind] += ua.count

        self.stdout.write("\nUnmapped/suspicious per supplier:")
        for sup, n in per_supplier.most_common():
            self.stdout.write(f"  {sup}: {n}")
        self.stdout.write("Per kind:")
        for kind, n in per_kind.most_common():
            self.stdout.write(f"  {kind}: {n}")

        top = options["top"]
        self.stdout.write(f"\nTop {top} unmapped codes:")
        for ua in qs.order_by("-count")[:top]:
            samples = ", ".join(str(s) for s in (ua.sample_values or [])[:3])
            sup = ua.supplier.code if ua.supplier else "-"
            self.stdout.write(
                f"  {ua.count:>5}  [{ua.kind}] {ua.code}  "
                f"({sup} / {ua.category_raw or '-'})  samples: {samples}"
            )
