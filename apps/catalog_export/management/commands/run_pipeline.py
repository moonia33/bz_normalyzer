import json

from django.core.management.base import BaseCommand, CommandError

from apps.catalog_export.models import PipelineRun
from apps.catalog_export.pipeline import (
    baseline_catalog,
    pipeline_lock,
    run_due,
    run_stage,
)
from apps.suppliers.models import Supplier


class Command(BaseCommand):
    help = (
        "Tiekėjų pipeline (katalogas / kainos / likučiai) į shop.\n"
        "--due: visi atėję etapai pagal SupplierSchedule (Coolify scheduled task kas 15 min.).\n"
        "--supplier X --stage catalog|prices|stock: vienas etapas rankiniu būdu.\n"
        "--supplier X --baseline: shop'e jau esančias prekes pažymėti kaip sinchronizuotas."
    )

    def add_arguments(self, parser):
        parser.add_argument("--due", action="store_true")
        parser.add_argument("--supplier")
        parser.add_argument("--stage", choices=[s.value for s in PipelineRun.Stage])
        parser.add_argument("--baseline", action="store_true")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Be feed'ų importo ir siuntimo – tik parodyti, kas būtų siunčiama",
        )

    def handle(self, *args, **options):
        if options["due"]:
            runs = run_due(dry_run=options["dry_run"])
            if runs is None:
                self.stdout.write("Kitas pipeline dar vykdomas – praleidžiama.")
                return
            if not runs:
                self.stdout.write("Nėra etapų, kuriems atėjo laikas.")
            self._report(runs)
            return

        code = options.get("supplier")
        if not code:
            raise CommandError("Nurodykite --due arba --supplier.")
        supplier = Supplier.objects.filter(code=code).first()
        if supplier is None:
            raise CommandError(f"Tiekėjas '{code}' nerastas.")
        if options["baseline"]:
            self.stdout.write(json.dumps(baseline_catalog(supplier), ensure_ascii=False))
            return
        if not options["stage"]:
            raise CommandError("Nurodykite --stage.")
        with pipeline_lock() as locked:
            if not locked:
                raise CommandError("Kitas pipeline dar vykdomas.")
            run = run_stage(supplier, options["stage"], dry_run=options["dry_run"])
        self._report([run])

    def _report(self, runs):
        failed = []
        for run in runs:
            self.stdout.write(
                f"{run.supplier.code}:{run.stage} #{run.pk} {run.status} "
                + json.dumps(run.details, ensure_ascii=False, default=str)[:4000]
            )
            if run.status == PipelineRun.Status.FAILED:
                failed.append(f"{run.supplier.code}:{run.stage}: {run.error_message}")
        if failed:
            raise CommandError("; ".join(failed))
