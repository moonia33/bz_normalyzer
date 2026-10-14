from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0007_alter_product_sub_category_raw"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="category_raw",
            field=models.CharField(blank=True, max_length=500, verbose_name="Suplier category"),
        ),
    ]
