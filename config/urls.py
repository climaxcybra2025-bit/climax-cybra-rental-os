from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from apps.web import views as web_views

admin.site.site_header = "Rental OS"
admin.site.site_title = "Rental OS"
admin.site.index_title = "Back office"


def health(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("", web_views.home, name="home"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path("app/", include("apps.web.urls")),
    path("portal/", include("apps.portal.urls")),
    path("admin/", admin.site.urls),
    path("api/", include("apps.api.urls")),
    path("health/", health),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
