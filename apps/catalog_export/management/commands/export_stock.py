import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.catalog_export.models import ExportRun
from apps.catalog_export.services import build_stock_payload, run_export


class Command(BaseCommand):
    help = "Sugeneruoja likučių feed'ą (format_version=1) vienam tiekėjui ir/arba nusiunčia į shop."

    def add_arguments(self, parser):
        parser.add_argument("--supplier", required=True, help="Tiekėjo kodas")
        parser.add_argument("--out", help="Išvesties JSON failo kelias")
        parser.add_argument("--push", action="store_true", help="POST į shop ops API")
        parser.add_argument("--dry-run", action="store_true", help="Tik statistika")

    def handle(self, *args, **options):
        payload = build_stock_payload(options["supplier"])
        self.stdout.write(
            f"Warehouse: {payload['warehouse_code']} | items: {len(payload['items'])}"
        )
        if options["dry_run"]:
            self.stdout.write(json.dumps(payload["items"][:5], indent=2, ensure_ascii=False))
            return

        file_name = ""
        if options.get("out"):
            path = Path(options["out"])
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            file_name = str(path)
            self.stdout.write(f"Įrašyta: {file_name}")

        if not options["out"] and not options["push"]:
            raise CommandError("Nurodykite --out <failas> arba --push.")

        run = run_export(
            ExportRun.Kind.STOCK,
            payload,
            push=options["push"],
            file_name=file_name,
            triggered_by="cli:export_stock",
            options={"supplier": options["supplier"]},
        )
        self.stdout.write(f"ExportRun #{run.pk}: {run.status}")
        if run.error_message:
            raise CommandError(run.error_message)
