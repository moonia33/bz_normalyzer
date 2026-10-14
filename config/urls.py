from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

from apps.api.api import api

admin.site.site_header = "Normalyzer"
admin.site.site_title = "Normalyzer"
admin.site.index_title = "Tiekėjų prekių normalizavimas"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
