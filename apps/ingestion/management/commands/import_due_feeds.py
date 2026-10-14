from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.ingestion.models import ImportJob
from apps.ingestion.pipeline import expand_feed_to_group, run_import
from apps.suppliers.models import SupplierFeed


class Command(BaseCommand):
    help = (
        "Importuoja visus feed'us, kuriems atėjo laikas pagal sync_interval_minutes. "
        "Skirta cron / Windows Task Scheduler (pvz. kas 15 min.). "
        "Kalbiniai feed'ai importuojami kartu su visa grupe."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Tik parodyti, kas būtų importuota")

    def handle(self, *args, **options):
        now = timezone.now()
        due = [feed for feed in SupplierFeed.objects.filter(is_active=True) if feed.is_due(now)]

        # Jei grupėje bent vienas feed'as due – importuojame visą grupę
        # (kalbos visada eina kartu).
        expanded: list[SupplierFeed] = []
        seen: set[int] = set()
        for feed in due:
            for group_feed in expand_feed_to_group(feed):
                if group_feed.pk not in seen and group_feed.is_active:
                    seen.add(group_feed.pk)
                    expanded.append(group_feed)

        if not expanded:
            self.stdout.write("Nėra feed'ų, kuriems atėjo laikas.")
            return

        for feed in expanded:
            self.stdout.write(f"Due: {feed} (interval={feed.sync_interval_minutes}min)")
            if options["dry_run"]:
                continue
            job = ImportJob.objects.create(supplier=feed.supplier, feed=feed)
            run_import(job)
            self.stdout.write(
                self.style.SUCCESS(f"  -> Job #{job.pk} {job.status} {job.stats}")
            )
