from django.test import TestCase

from apps.catalog.models import Category
from apps.mapping.models import CategoryMapping
from apps.mapping.services import map_category
from apps.suppliers.models import Supplier


class MappingTests(TestCase):
    def test_maps_exact_source_path(self):
        supplier = Supplier.objects.create(code="demo", name="Demo")
        category = Category.objects.create(name="Meškerės")
        CategoryMapping.objects.create(
            supplier=supplier,
            source_category_raw="Rods",
            source_sub_category_raw="Spinning",
            source_path="Rods > Spinning",
            category=category,
        )
        self.assertEqual(map_category(supplier, "Rods", "Spinning"), category)
        self.assertIsNone(map_category(supplier, "Unknown", ""))
        self.assertTrue(
            CategoryMapping.objects.filter(
                supplier=supplier,
                source_category_raw="Unknown",
                source_sub_category_raw="",
            ).exists()
        )

    def test_falls_back_to_root_category_when_deep_path_is_unmapped(self):
        supplier = Supplier.objects.create(code="demo2", name="Demo 2")
        root_category = Category.objects.create(name="Accessories")
        CategoryMapping.objects.create(
            supplier=supplier,
            source_category_raw="Accessories",
            source_sub_category_raw="",
            source_path="Accessories",
            category=root_category,
        )
        self.assertEqual(map_category(supplier, "Accessories", "Bait Accessories"), root_category)

    def test_does_not_duplicate_source_path_when_category_already_contains_leaf(self):
        supplier = Supplier.objects.create(code="demo3", name="Demo 3")
        self.assertIsNone(map_category(supplier, "Groundbait > Groundbait", "Groundbait"))
        mapping = CategoryMapping.objects.get(supplier=supplier, source_category_raw="Groundbait")
        self.assertEqual(mapping.source_sub_category_raw, "Groundbait")
        self.assertEqual(mapping.source_path, "Groundbait > Groundbait")
