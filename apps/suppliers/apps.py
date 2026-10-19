from django.apps import AppConfig


class SuppliersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.suppliers"
    verbose_name = "Tiekėjai"

    def ready(self) -> None:
        from apps.suppliers.adapters import (  # noqa: F401
            fox,
            fox_discontinued,
            fox_stock,
            generic_excel,
            generic_json,
            generic_xml,
            korda,
            korda_info,
            moss,
        )
