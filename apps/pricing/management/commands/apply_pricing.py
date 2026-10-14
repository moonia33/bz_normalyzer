from django.core.management.base import BaseCommand

from apps.pricing.engine import apply_pricing


class Command(BaseCommand):
    help = "Perskaičiuoja visų pasiūlymų pardavimo kainas pagal taisykles."

    def handle(self, *args, **options):
        count = apply_pricing()
        self.stdout.write(self.style.SUCCESS(f"Atnaujinta kainų: {count}"))
