from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("ingestion", "0005_offer_is_discontinued"),
    ]

    operations = [
        migrations.RemoveField(model_name="offer", name="bullet_01"),
        migrations.RemoveField(model_name="offer", name="bullet_02"),
        migrations.RemoveField(model_name="offer", name="bullet_03"),
        migrations.RemoveField(model_name="offer", name="bullet_04"),
        migrations.RemoveField(model_name="offer", name="bullet_05"),
        migrations.RemoveField(model_name="offer", name="bullet_06"),
        migrations.RemoveField(model_name="offer", name="bullet_07"),
        migrations.RemoveField(model_name="offer", name="bullet_08"),
        migrations.RemoveField(model_name="offer", name="bullet_09"),
        migrations.RemoveField(model_name="offer", name="bullet_10"),
    ]
