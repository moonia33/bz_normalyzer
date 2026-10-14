from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0006_product_categories"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="sub_category_raw",
            field=models.CharField(blank=True, max_length=200, verbose_name="Suplier sub category"),
        ),
    ]
