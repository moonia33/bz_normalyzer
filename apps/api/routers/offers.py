from ninja import Query, Router
from ninja.pagination import paginate

from apps.api.pagination import CatalogPagination
from apps.api.schemas import OfferOut
from apps.ingestion.models import Offer

router = Router(tags=["Pasiūlymai"])


@router.get("/", response=list[OfferOut])
@paginate(CatalogPagination)
def list_offers(
    request,
    supplier: str | None = Query(None, description="Tiekėjo kodas"),
    q: str | None = Query(None),
    available_only: bool = True,
    category_id: int | None = None,
):
    qs = Offer.objects.select_related("supplier", "brand", "category", "product").filter(
        is_published=True
    )
    if available_only:
        qs = qs.filter(is_available=True)
    if supplier:
        qs = qs.filter(supplier__code=supplier)
    if category_id:
        qs = qs.filter(category_id=category_id)
    if q:
        qs = qs.filter(name__icontains=q)
    return qs


@router.get("/{offer_id}", response=OfferOut)
def get_offer(request, offer_id: int):
    return Offer.objects.select_related("supplier", "brand", "category").get(
        pk=offer_id, is_published=True
    )
