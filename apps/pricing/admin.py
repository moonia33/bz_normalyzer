from django.contrib import admin

from apps.pricing.engine import apply_pricing
from apps.pricing.models import PriceRule


@admin.register(PriceRule)
class PriceRuleAdmin(admin.ModelAdmin):
    list_display = [
        "priority",
        "name",
        "action",
        "value",
        "supplier",
        "category",
        "brand",
        "is_active",
    ]
    list_filter = ["is_active", "action", "supplier"]
    list_display_links = ["name"]
    list_editable = ["priority", "is_active"]
    actions = ["recalculate_all"]
    ordering = ["priority"]

    @admin.action(description="Perskaičiuoti visas pasiūlymų kainas")
    def recalculate_all(self, request, queryset):
        count = apply_pricing()
        self.message_user(request, f"Atnaujinta kainų: {count}")
