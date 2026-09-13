from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    # The student portal owns every /portal/ route and is a separate
    # jurisdiction from the staff console below it.
    path("", include("apps.portal.urls")),
    path("", include("apps.academy.urls")),
    path("", include("apps.research.urls")),
    path("", include("apps.assignments.urls")),
    path("", include("apps.youtube.urls")),
    path("", include("apps.monitoring.urls")),
    # Core last: it owns the bare "" dashboard route and the phase placeholders,
    # so more specific app routes get first refusal.
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = "100DaysAI Academy Tracker"
admin.site.site_title = "Academy Tracker"
admin.site.index_title = "Administration"
