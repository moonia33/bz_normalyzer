from django.contrib import admin

from apps.corecfg.models import ConfigEntry, Language


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "is_base", "is_active", "position"]
    list_editable = ["is_active", "position"]


@admin.register(ConfigEntry)
class ConfigEntryAdmin(admin.ModelAdmin):
    list_display = ["key", "value", "description"]
    search_fields = ["key"]
