from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ingestion", "0006_remove_offer_bullet_01_remove_offer_bullet_02_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="offer",
            name="category_raw",
            field=models.CharField(blank=True, max_length=500, verbose_name="Suplier category"),
        ),
        migrations.AlterField(
            model_name="offer",
            name="sub_category_raw",
            field=models.CharField(blank=True, max_length=200, verbose_name="Suplier sub category"),
        ),
    ]
