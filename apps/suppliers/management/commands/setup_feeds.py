from django.core.management.base import BaseCommand

from apps.corecfg.models import Language
from apps.suppliers.models import FeedRole, SourceKind, Supplier, SupplierFeed

LANGUAGES = [
    {"code": "en", "name": "Anglų", "is_base": True, "position": 10},
    {"code": "lt", "name": "Lietuvių", "is_base": False, "position": 20},
    {"code": "pl", "name": "Lenkų", "is_base": False, "position": 30},
    {"code": "de", "name": "Vokiečių", "is_base": False, "position": 40},
]

FOX_BASE_URL = "https://order.foxtackle.biz/download/FoxPimFiles"
CCM_BASE_URL = "https://order.foxtackle.biz/download/CcMoorePimFiles"

# (supplier, adapter_code, feed_group, {lang: url|None}, purchase_includes_vat)
PIM_SUPPLIERS = [
    {
        "code": "fox",
        "name": "FOX International",
        "adapter_code": "fox",
        "feed_group": "pricelist",
        "purchase_price_includes_vat": False,
        "feeds": {
            "en": f"{FOX_BASE_URL}/GB_FOX_EU_pricelist.xlsx",
            "pl": f"{FOX_BASE_URL}/PL_FOX_EU_pricelist.xlsx",
            "de": f"{FOX_BASE_URL}/DE_FOX_EU_pricelist.xlsx",
        },
    },
    {
        "code": "ccm",
        "name": "CC Moore",
        "adapter_code": "fox",  # Tas pats PIM Excel formatas
        "feed_group": "pricelist",
        "purchase_price_includes_vat": False,
        "feeds": {
            "en": f"{CCM_BASE_URL}/GB_CCM_EU_pricelist.xlsx",
            "pl": f"{CCM_BASE_URL}/PL_CCM_EU_pricelist.xlsx",
            "de": f"{CCM_BASE_URL}/DE_CCM_EU_pricelist.xlsx",
        },
    },
]

MOSS_URLS = {
    "en": "https://moss.sk/moss-feed-en.xml",
    "pl": "https://moss.sk/moss-feed-pl.xml",
    "de": "https://moss.sk/moss-feed-de.xml",
}

DEFAULT_SYNC_MINUTES = 60 * 24  # kasdien


class Command(BaseCommand):
    help = "Sukuria/atnaujina kalbas, tiekėjus ir jų feed'us (idempotentiška)."

    def handle(self, *args, **options):
        languages = {}
        for spec in LANGUAGES:
            lang, created = Language.objects.update_or_create(
                code=spec["code"], defaults=spec
            )
            languages[lang.code] = lang
            self.stdout.write(f"Language {lang.code}: {'created' if created else 'updated'}")

        for spec in PIM_SUPPLIERS:
            supplier, _ = Supplier.objects.update_or_create(
                code=spec["code"],
                defaults={
                    "name": spec["name"],
                    "purchase_price_includes_vat": spec["purchase_price_includes_vat"],
                    "selling_price_includes_vat": True,
                },
            )
            for lang_code, url in spec["feeds"].items():
                feed, created = SupplierFeed.objects.update_or_create(
                    supplier=supplier,
                    name=f"{spec['name']} {lang_code.upper()} pricelist",
                    defaults={
                        "kind": SourceKind.EXCEL_URL,
                        "role": FeedRole.CATALOG,
                        "language": languages[lang_code],
                        "feed_group": spec["feed_group"],
                        "url": url,
                        "adapter_code": spec["adapter_code"],
                        "sync_interval_minutes": DEFAULT_SYNC_MINUTES,
                        "is_active": True,
                    },
                )
                self.stdout.write(
                    f"Feed {feed}: {'created' if created else 'updated'}"
                )

        moss, _ = Supplier.objects.update_or_create(
            code="moss",
            defaults={
                "name": "Moss",
                "purchase_price_includes_vat": True,  # PRICE_VAT – su PVM
                "selling_price_includes_vat": True,
            },
        )
        for lang_code, url in MOSS_URLS.items():
            feed, created = SupplierFeed.objects.update_or_create(
                supplier=moss,
                name=f"Moss {lang_code.upper()} XML",
                defaults={
                    "kind": SourceKind.XML_URL,
                    "role": FeedRole.CATALOG,
                    "language": languages[lang_code],
                    "feed_group": "catalog",
                    "url": url,
                    "adapter_code": "moss",
                    "sync_interval_minutes": DEFAULT_SYNC_MINUTES,
                    "is_active": True,
                },
            )
            self.stdout.write(f"Feed {feed}: {'created' if created else 'updated'}")

        self.stdout.write(self.style.SUCCESS("Done."))
