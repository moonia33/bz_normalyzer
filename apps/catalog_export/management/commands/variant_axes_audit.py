import json
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from apps.catalog.models import Category, Product
from apps.catalog.profiles import DEFAULT_PROFILE, profile_for
from apps.catalog_export import services as export_svc
from apps.catalog_export.services import (
    _code,
    _offer_context,
    _plan_variants,
)


class Command(BaseCommand):
    help = (
        "Dry-run option profilių auditas: per profilį grupių statistika, "
        "'varying bet ne profile.axes' kodai ir all-or-nothing nunulintos "
        "grupės. Niekas nerašoma į DB."
    )

    def add_arguments(self, parser):
        parser.add_argument("--supplier", help="Tik vieno tiekėjo prekės")
        parser.add_argument("--json", action="store_true", help="JSON išvestis")

    def handle(self, *args, **options):
        supplier_code = options.get("supplier")
        export_svc._load_category_names()
        try:
            products_qs = (
                Product.objects.filter(is_active=True)
                .select_related("brand", "category")
                .prefetch_related("offers__supplier")
                .filter(offers__is_published=True)
                .distinct()
            )
            if supplier_code:
                products_qs = products_qs.filter(
                    offers__supplier__code=supplier_code
                )

            stats = defaultdict(lambda: {
                "groups": 0,
                "with_options": 0,
                "zeroed": 0,
                "no_varying": 0,
                "not_allowed": Counter(),
            })
            zeroed_groups = []

            for product in products_qs.iterator(chunk_size=500):
                offers = [
                    o
                    for o in product.offers.all()
                    if o.is_published and not o.is_discontinued
                ]
                if supplier_code:
                    offers = [
                        o for o in offers if o.supplier.code == supplier_code
                    ]
                if len(offers) <= 1:
                    continue

                profile = profile_for(_offer_context(offers[0]))
                allowed = {_code(ax) for ax in profile.axes}
                options_by_offer, _d, _l, variant_features, _m = (
                    _plan_variants(offers, profile)
                )
                varying = set()
                for metas in variant_features.values():
                    varying.update(metas)
                chosen = set()
                for opts in options_by_offer.values():
                    chosen.update(opts)
                not_allowed = varying - allowed
                st = stats[profile.code]
                st["groups"] += 1
                if not varying:
                    st["no_varying"] += 1
                if chosen:
                    st["with_options"] += 1
                elif allowed & varying:
                    st["zeroed"] += 1
                    zeroed_groups.append(
                        {
                            "product": product.name,
                            "sku": product.sku,
                            "profile": profile.code,
                            "varying": sorted(varying),
                            "blocked": sorted(allowed & varying),
                        }
                    )
                st["not_allowed"].update(not_allowed)
        finally:
            export_svc._CATEGORY_NAMES.clear()

        result = {
            "per_profile": {
                code: {
                    **{k: v for k, v in st.items() if k != "not_allowed"},
                    "not_allowed_top": st["not_allowed"].most_common(10),
                }
                for code, st in stats.items()
            },
            "zeroed_groups": zeroed_groups[:30],
        }
        if options["json"]:
            self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
            return

        self.stdout.write("Per profile:")
        for code, st in sorted(
            stats.items(), key=lambda kv: -kv[1]["groups"]
        ):
            self.stdout.write(
                f"  {code:10} groups={st['groups']:>5} "
                f"with_options={st['with_options']:>5} "
                f"zeroed={st['zeroed']:>4} no_varying={st['no_varying']:>4}"
            )
            top = st["not_allowed"].most_common(10)
            if top:
                self.stdout.write(
                    "    varying-not-allowed: "
                    + ", ".join(f"{c}×{n}" for c, n in top)
                )
        self.stdout.write("\nZeroed (all-or-nothing) groups, max 30:")
        for g in zeroed_groups[:30]:
            self.stdout.write(
                f"  [{g['profile']}] {g['sku']} {g['product'][:60]} "
                f"varying={','.join(g['varying'])}"
            )
