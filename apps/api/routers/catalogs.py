from ninja import Query, Router
from ninja.pagination import paginate

from apps.api.pagination import CatalogPagination
from apps.api.schemas import CategoryOut, ProductOut, SupplierOut
from apps.catalog.models import Category, Product
from apps.suppliers.models import Supplier

router = Router(tags=["Kategorijos"])
products_router = Router(tags=["Prekės"])
suppliers_router = Router(tags=["Tiekėjai"])


@router.get("/", response=list[CategoryOut])
def list_categories(request, active: bool = True):
    return Category.objects.select_related("parent").filter(is_active=active)


@products_router.get("/", response=list[ProductOut])
@paginate(CatalogPagination)
def list_products(request, q: str | None = Query(None), category_id: int | None = None):
    qs = Product.objects.select_related("brand", "category").filter(is_active=True)
    if q:
        qs = qs.filter(name__icontains=q)
    if category_id:
        qs = qs.filter(category_id=category_id)
    return qs


@products_router.get("/{product_id}", response=ProductOut)
def get_product(request, product_id: int):
    return Product.objects.select_related("brand", "category").get(pk=product_id)


@suppliers_router.get("/", response=list[SupplierOut])
def list_suppliers(request, active_only: bool = True):
    qs = Supplier.objects.all()
    if active_only:
        qs = qs.filter(is_active=True)
    return qs
