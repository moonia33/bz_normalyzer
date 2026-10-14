import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.catalog_export.models import ExportRun
from apps.catalog_export.services import build_catalog_payload, run_export


class Command(BaseCommand):
    help = "Sugeneruoja canonical katalogo JSON (format_version=1) ir/arba nusiunčia į shop."

    def add_arguments(self, parser):
        parser.add_argument("--out", help="Išvesties JSON failo kelias")
        parser.add_argument("--supplier", help="Eksportuoti tik vieno tiekėjo prekes")
        parser.add_argument("--push", action="store_true", help="POST į shop ops API")
        parser.add_argument("--dry-run", action="store_true", help="Tik parodyti statistiką, nieko nerašyti")

    def handle(self, *args, **options):
        payload = build_catalog_payload(supplier_code=options.get("supplier"))
        count = len(payload["items"])
        self.stdout.write(f"Items: {count}")

        if options["dry_run"]:
            self.stdout.write(json.dumps(payload["items"][:2], indent=2, ensure_ascii=False, default=str))
            return

        file_name = ""
        if options.get("out"):
            path = Path(options["out"])
            path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
            file_name = str(path)
            self.stdout.write(f"Įrašyta: {file_name}")

        if not options["out"] and not options["push"]:
            raise CommandError("Nurodykite --out <failas> arba --push (arba --dry-run).")

        run = run_export(
            ExportRun.Kind.CATALOG,
            payload,
            push=options["push"],
            file_name=file_name,
            triggered_by="cli:export_catalog",
            options={"supplier": options.get("supplier") or ""},
        )
        self.stdout.write(f"ExportRun #{run.pk}: {run.status}")
        if run.error_message:
            raise CommandError(run.error_message)
