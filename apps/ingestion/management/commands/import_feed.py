from django.core.management.base import BaseCommand, CommandError

from apps.ingestion.models import ImportJob
from apps.ingestion.pipeline import run_import
from apps.suppliers.models import Supplier, SupplierFeed


class Command(BaseCommand):
    help = "Importuoja tiekėjo šaltinį ir atnaujina normalizuotus pasiūlymus."

    def add_arguments(self, parser):
        parser.add_argument("--supplier", required=True, help="Tiekėjo kodas (slug)")
        parser.add_argument("--feed", type=int, help="Konkretaus SupplierFeed id")

    def handle(self, *args, **options):
        try:
            supplier = Supplier.objects.get(code=options["supplier"])
        except Supplier.DoesNotExist as exc:
            raise CommandError(f"Tiekėjas nerastas: {options['supplier']}") from exc
        feed = None
        if options.get("feed"):
            try:
                feed = SupplierFeed.objects.get(pk=options["feed"], supplier=supplier)
            except SupplierFeed.DoesNotExist as exc:
                raise CommandError("Šaltinis nerastas šiam tiekėjui.") from exc
            job = ImportJob.objects.create(supplier=supplier, feed=feed)
            run_import(job)
            self.stdout.write(self.style.SUCCESS(f"Job #{job.pk} {job.status} {job.stats}"))
            if job.status == ImportJob.Status.FAILED:
                raise CommandError(job.error_message)
            return

        feeds = list(supplier.feeds.filter(is_active=True).order_by("name"))
        if not feeds:
            raise CommandError("Tiekėjas neturi aktyvių feed'ų.")

        failures: list[str] = []
        for active_feed in feeds:
            job = ImportJob.objects.create(supplier=supplier, feed=active_feed)
            run_import(job)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Feed #{active_feed.pk} ({active_feed.name}) -> "
                    f"Job #{job.pk} {job.status} {job.stats}"
                )
            )
            if job.status == ImportJob.Status.FAILED:
                failures.append(f"[{active_feed.name}] {job.error_message}")

        if failures:
            raise CommandError("Kai kurie feed'ai nesėkmingi:\n- " + "\n- ".join(failures))
