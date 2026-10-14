import django.db.models.deletion
from django.db import migrations, models


def _forward_fill_source_category_fields(apps, schema_editor):
    CategoryMapping = apps.get_model("mapping", "CategoryMapping")
    for mapping in CategoryMapping.objects.all().iterator():
        source_path = (mapping.source_path or "").strip()
        source_category = ""
        source_sub_category = ""
        if source_path:
            parts = [part.strip() for part in source_path.split(">") if part.strip()]
            if parts:
                source_category = parts[0]
                source_sub_category = " > ".join(parts[1:])
        mapping.source_category_raw = source_category
        mapping.source_sub_category_raw = source_sub_category
        mapping.save(update_fields=["source_category_raw", "source_sub_category_raw", "updated_at"])


def _reverse_fill_source_path(apps, schema_editor):
    CategoryMapping = apps.get_model("mapping", "CategoryMapping")
    for mapping in CategoryMapping.objects.all().iterator():
        source_category = (mapping.source_category_raw or "").strip()
        source_sub_category = (mapping.source_sub_category_raw or "").strip()
        if source_category and source_sub_category:
            source_path = f"{source_category} > {source_sub_category}"
        else:
            source_path = source_category
        mapping.source_path = source_path
        mapping.save(update_fields=["source_path", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0006_product_categories"),
        ("mapping", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="categorymapping",
            name="source_category_raw",
            field=models.CharField(default="", max_length=200),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="categorymapping",
            name="source_sub_category_raw",
            field=models.CharField(blank=True, default="", max_length=200),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="categorymapping",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="source_mappings",
                to="catalog.category",
            ),
        ),
        migrations.AddField(
            model_name="categorymapping",
            name="sub_category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="source_sub_mappings",
                to="catalog.category",
            ),
        ),
        migrations.RunPython(
            _forward_fill_source_category_fields,
            reverse_code=_reverse_fill_source_path,
        ),
        migrations.AddConstraint(
            model_name="categorymapping",
            constraint=models.UniqueConstraint(
                fields=("supplier", "source_category_raw", "source_sub_category_raw"),
                name="uniq_category_mapping_supplier_category_subcategory",
            ),
        ),
    ]
