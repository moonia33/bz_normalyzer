from django.core.exceptions import ObjectDoesNotExist
from ninja import NinjaAPI

from apps.api.auth import ApiKeyAuth
from apps.api.routers import catalogs, imports, offers

api = NinjaAPI(
    title="Normalyzer API",
    version="1.0.0",
    description="Vieningas tiekėjų prekių katalogas.",
    auth=ApiKeyAuth(),
)

api.add_router("/categories", catalogs.router)
api.add_router("/products", catalogs.products_router)
api.add_router("/suppliers", catalogs.suppliers_router)
api.add_router("/offers", offers.router)
api.add_router("/imports", imports.router)


@api.exception_handler(ObjectDoesNotExist)
def on_not_found(request, exc):
    return api.create_response(request, {"detail": "Nerasta"}, status=404)
