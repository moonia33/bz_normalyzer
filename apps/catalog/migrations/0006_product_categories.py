from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0005_remove_product_bullet_01_remove_product_bullet_02_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="categories",
            field=models.ManyToManyField(blank=True, related_name="products_assigned", to="catalog.category"),
        ),
    ]
