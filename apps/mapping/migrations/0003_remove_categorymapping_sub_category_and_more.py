from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mapping", "0002_categorymapping_supplier_category_fields"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="categorymapping",
            name="sub_category",
        ),
        migrations.AlterField(
            model_name="categorymapping",
            name="source_category_raw",
            field=models.CharField(max_length=200, verbose_name="Suplier category"),
        ),
        migrations.AlterField(
            model_name="categorymapping",
            name="source_path",
            field=models.CharField(
                help_text="Tiksli tiekėjo kategorijos eilutė, pvz. 'Meškerės > Spinningas'.",
                max_length=500,
                verbose_name="Suplier category > subcategory",
            ),
        ),
        migrations.AlterField(
            model_name="categorymapping",
            name="source_sub_category_raw",
            field=models.CharField(blank=True, max_length=200, verbose_name="Suplier sub category"),
        ),
    ]
