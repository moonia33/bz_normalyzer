from django.core.management.base import BaseCommand

from apps.catalog.models import Category
from apps.catalog_export.services import ShopClient


class Command(BaseCommand):
    help = (
        "Susinchronizuoja normalyzerio Category medį su shop kategorijomis "
        "(slug sutampa su shop Category.slug – eksportui)."
    )

    def handle(self, *args, **options):
        client = ShopClient()
        rows = client.get("/api/v1/ops/imports/categories")
        self.stdout.write(f"Shop categories: {len(rows)}")

        # 1) Upsert pagal slug (be parent'ų – antra iteracija).
        # Dėmesio: Category.slug normalyzeryje unikalus tik (parent, slug) – shop'e globaliai.
        by_slug: dict[str, Category] = {}
        created = updated = 0
        for row in rows:
            cat = Category.objects.filter(slug=row["slug"]).order_by("id").first()
            was_created = False
            if cat is None:
                cat = Category.objects.create(
                    slug=row["slug"], name=row["name"], is_active=row["is_active"]
                )
                was_created = True
            else:
                changed = []
                if cat.name != row["name"]:
                    cat.name = row["name"]
                    changed.append("name")
                if cat.is_active != row["is_active"]:
                    cat.is_active = row["is_active"]
                    changed.append("is_active")
                if changed:
                    cat.save(update_fields=[*changed, "updated_at"])
            by_slug[row["slug"]] = cat
            created += int(was_created)
            updated += int(not was_created)

        # 2) Parent'ai antra iteracija (parent_slug -> obj)
        for row in rows:
            parent_slug = row.get("parent_slug") or ""
            cat = by_slug[row["slug"]]
            parent = by_slug.get(parent_slug) if parent_slug else None
            if cat.parent_id != (parent.id if parent else None):
                cat.parent = parent
                cat.save(update_fields=["parent"])

        self.stdout.write(
            self.style.SUCCESS(f"Done: {created} sukurta, {updated} atnaujinta.")
        )
