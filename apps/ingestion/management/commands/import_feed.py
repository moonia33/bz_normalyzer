from django.core.management.base import BaseCommand, CommandError

from apps.ingestion.models import ImportJob
from apps.ingestion.pipeline import expand_feed_to_group, run_import
from apps.suppliers.models import Supplier, SupplierFeed


def _run_feed(feed: SupplierFeed, stdout, style) -> str | None:
    """Paleidžia vieną feed'ą. Grąžina klaidos tekstą arba None."""
    job = ImportJob.objects.create(supplier=feed.supplier, feed=feed)
    run_import(job)
    stdout.write(
        style.SUCCESS(
            f"Feed #{feed.pk} ({feed.name}) -> Job #{job.pk} {job.status} {job.stats}"
        )
    )
    if job.status == ImportJob.Status.FAILED:
        return f"[{feed.name}] {job.error_message}"
    return None


class Command(BaseCommand):
    help = "Importuoja tiekėjo šaltinį ir atnaujina normalizuotus pasiūlymus."

    def add_arguments(self, parser):
        parser.add_argument("--supplier", required=True, help="Tiekėjo kodas (slug)")
        parser.add_argument("--feed", type=int, help="Konkretaus SupplierFeed id (kartu importuojama visa feed grupė)")

    def handle(self, *args, **options):
        try:
            supplier = Supplier.objects.get(code=options["supplier"])
        except Supplier.DoesNotExist as exc:
            raise CommandError(f"Tiekėjas nerastas: {options['supplier']}") from exc

        if options.get("feed"):
            try:
                feed = SupplierFeed.objects.get(pk=options["feed"], supplier=supplier)
            except SupplierFeed.DoesNotExist as exc:
                raise CommandError("Šaltinis nerastas šiam tiekėjui.") from exc
            feeds = expand_feed_to_group(feed)
        else:
            feeds = list(supplier.feeds.filter(is_active=True))
            if not feeds:
                raise CommandError("Tiekėjas neturi aktyvių feed'ų.")
            # Catalog feed'ai pirmiausia – stock/discontinued tik atnaujina
            # jau sukurtus offer'ius (role!=catalog -> partial update).
            role_rank = {"catalog": 0, "stock": 1, "discontinued": 2}
            feeds.sort(key=lambda f: (role_rank.get(f.role or "catalog", 0), f.name))
            # Grupės išskleidžiame: jei feed'as priklauso grupei – imame visą grupę
            # (bazinė kalba pirmiausia), be pasikartojimų.
            expanded: list[SupplierFeed] = []
            seen: set[int] = set()
            for feed in feeds:
                for group_feed in expand_feed_to_group(feed):
                    if group_feed.pk not in seen:
                        seen.add(group_feed.pk)
                        expanded.append(group_feed)
            feeds = expanded

        failures: list[str] = []
        for active_feed in feeds:
            error = _run_feed(active_feed, self.stdout, self.style)
            if error:
                failures.append(error)

        if failures:
            raise CommandError("Kai kurie feed'ai nesėkmingi:\n- " + "\n- ".join(failures))
