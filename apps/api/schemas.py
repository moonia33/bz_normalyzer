from datetime import datetime
from decimal import Decimal

from ninja import ModelSchema, Schema

from apps.catalog.models import Brand, Category, Product
from apps.ingestion.models import ImportJob, Offer
from apps.suppliers.models import Supplier


class CategoryOut(ModelSchema):
    parent_id: int | None = None
    path: str

    class Meta:
        model = Category
        fields = ["id", "name", "slug"]

    @staticmethod
    def resolve_path(obj: Category) -> str:
        return obj.path()


class BrandOut(ModelSchema):
    class Meta:
        model = Brand
        fields = ["id", "name", "slug"]


class ProductOut(ModelSchema):
    brand: BrandOut | None = None
    category: CategoryOut | None = None

    class Meta:
        model = Product
        fields = [
            "id",
            "sku",
            "name",
            "ean",
            "description",
            "size_raw",
            "package_raw",
            "unit_weight_raw",
            "shipping_height_cm",
            "shipping_width_cm",
            "shipping_depth_cm",
            "shipping_weight_kg",
            "attributes",
            "is_active",
        ]


class OfferOut(ModelSchema):
    supplier_code: str
    brand_name: str
    category_path: str
    purchase_price: Decimal | None
    selling_price: Decimal | None
    last_imported_at: datetime | None

    class Meta:
        model = Offer
        fields = [
            "id",
            "external_id",
            "sku",
            "name",
            "ean",
            "description",
            "attributes",
            "images",
            "stock",
            "availability_date",
            "currency",
            "is_available",
            "is_discontinued",
            "category_raw",
            "sub_category_raw",
            "size_raw",
            "package_raw",
            "unit_weight_raw",
            "shipping_height_cm",
            "shipping_width_cm",
            "shipping_depth_cm",
            "shipping_weight_kg",
        ]

    @staticmethod
    def resolve_supplier_code(obj: Offer) -> str:
        return obj.supplier.code

    @staticmethod
    def resolve_brand_name(obj: Offer) -> str:
        return obj.brand.name if obj.brand_id else obj.brand_name_raw

    @staticmethod
    def resolve_category_path(obj: Offer) -> str:
        return obj.category.path() if obj.category_id else obj.category_raw


class SupplierOut(ModelSchema):
    class Meta:
        model = Supplier
        fields = ["id", "code", "name", "is_active"]


class ImportJobOut(ModelSchema):
    supplier_code: str

    class Meta:
        model = ImportJob
        fields = ["id", "status", "stats", "error_message", "started_at", "finished_at"]

    @staticmethod
    def resolve_supplier_code(obj: ImportJob) -> str:
        return obj.supplier.code


class MessageOut(Schema):
    detail: str
