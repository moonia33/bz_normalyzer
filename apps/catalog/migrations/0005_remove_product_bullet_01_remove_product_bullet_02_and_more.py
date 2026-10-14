from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_product_images"),
    ]

    operations = [
        migrations.RemoveField(model_name="product", name="bullet_01"),
        migrations.RemoveField(model_name="product", name="bullet_02"),
        migrations.RemoveField(model_name="product", name="bullet_03"),
        migrations.RemoveField(model_name="product", name="bullet_04"),
        migrations.RemoveField(model_name="product", name="bullet_05"),
        migrations.RemoveField(model_name="product", name="bullet_06"),
        migrations.RemoveField(model_name="product", name="bullet_07"),
        migrations.RemoveField(model_name="product", name="bullet_08"),
        migrations.RemoveField(model_name="product", name="bullet_09"),
        migrations.RemoveField(model_name="product", name="bullet_10"),
    ]
