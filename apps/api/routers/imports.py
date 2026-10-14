from ninja import File, Router
from ninja.files import UploadedFile

from apps.api.schemas import ImportJobOut, MessageOut
from apps.ingestion.models import ImportJob
from apps.ingestion.pipeline import run_import
from apps.suppliers.models import Supplier, SupplierFeed

router = Router(tags=["Importas"])


@router.get("/", response=list[ImportJobOut])
def list_jobs(request, supplier: str | None = None, limit: int = 50):
    qs = ImportJob.objects.select_related("supplier").all()
    if supplier:
        qs = qs.filter(supplier__code=supplier)
    return qs[:limit]


@router.post("/{supplier_code}/run", response=ImportJobOut)
def run_supplier_import(request, supplier_code: str, feed_id: int | None = None):
    supplier = Supplier.objects.get(code=supplier_code, is_active=True)
    feed = None
    if feed_id:
        feed = SupplierFeed.objects.get(pk=feed_id, supplier=supplier)
    job = ImportJob.objects.create(supplier=supplier, feed=feed)
    run_import(job)
    job.refresh_from_db()
    return job


@router.post("/{supplier_code}/upload", response={200: ImportJobOut, 400: MessageOut})
def upload_and_import(request, supplier_code: str, file: UploadedFile = File(...)):
    supplier = Supplier.objects.get(code=supplier_code, is_active=True)
    feed = supplier.feeds.filter(is_active=True, kind__in=["excel_upload", "csv_upload"]).first()
    if feed is None:
        return 400, {"detail": "Tiekėjas neturi aktyvaus Excel/CSV įkėlimo šaltinio."}
    job = ImportJob.objects.create(supplier=supplier, feed=feed)
    job.uploaded_file.save(file.name, file)
    run_import(job)
    job.refresh_from_db()
    return job
