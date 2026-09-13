from django.urls import path

from apps.monitoring import views

app_name = "monitoring"

urlpatterns = [
    path("alerts/", views.alert_list, name="alert_list"),
    path("alerts/scan/", views.alert_scan, name="alert_scan"),
    path("alerts/<uuid:pk>/", views.alert_detail, name="alert_detail"),
    path("alerts/<uuid:pk>/update/", views.alert_update, name="alert_update"),
]
